"""軌跡產生管線、v0.1 入庫檢查器與補充 audit 的正負向測試。執行：python -m unittest discover -s tests -t ."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import audit_trajectory_dataset as audit
import build_fixtures
import generate_trajectories as gen
import stub_tools
import validate_trajectory_dataset as intake

FIXTURES = ROOT / "tests" / "fixtures"
DATA_ROOT = ROOT / "data" / "trajectories"
DATASET = DATA_ROOT / "zh-Hant"
MANIFEST = ROOT / "data" / "manifests" / "trajectories-zh-Hant.json"
SEEDS = ROOT / "data" / "seed-tasks-zh-Hant.json"


def _intake_codes(*paths: Path) -> set[str]:
    v = intake.Validator()
    for p in paths:
        if p.is_dir():
            v.validate_tree(p)
        else:
            v.validate_file(p, p.parent)
    return {f.code for f in v.findings if f.severity == "error"}


class SeedFileTest(unittest.TestCase):
    def test_seed_file_has_20_tasks_across_categories(self) -> None:
        doc = json.loads(SEEDS.read_text("utf-8"))
        self.assertGreaterEqual(len(doc["tasks"]), 20)
        self.assertEqual(set(gen.CATEGORIES), {t["category"] for t in doc["tasks"]})
        self.assertEqual(gen.check_seed_doc(doc), [])

    def test_seed_check_rejects_small_or_incomplete_doc(self) -> None:
        doc = json.loads(SEEDS.read_text("utf-8"))
        small = {**doc, "tasks": doc["tasks"][:3]}
        errs = gen.check_seed_doc(small)
        self.assertTrue(any("至少 20" in e for e in errs))
        self.assertTrue(any("缺少類別" in e for e in errs))
        broken = copy.deepcopy(doc)
        del broken["tasks"][0]["expected_state"]
        broken["tasks"][1]["permissions"]["allowed_tools"].append("shell_exec")
        errs = gen.check_seed_doc(broken)
        self.assertTrue(any("expected_state" in e for e in errs))
        self.assertTrue(any("shell_exec" in e for e in errs))


class GeneratorTest(unittest.TestCase):
    def test_render_is_deterministic(self) -> None:
        raw = SEEDS.read_bytes()
        self.assertEqual(gen.render(json.loads(raw), raw), gen.render(json.loads(raw), raw))

    def test_committed_dataset_and_fixtures_are_up_to_date(self) -> None:
        self.assertEqual(gen.main(["--check"]), 0)
        self.assertEqual(build_fixtures.main(["--check"]), 0)

    def test_every_trajectory_uses_v01_shape_and_replays(self) -> None:
        files = sorted(DATASET.glob("traj-*.json"))
        self.assertGreaterEqual(len(files), 20)
        for f in files:
            t = json.loads(f.read_text("utf-8"))
            self.assertEqual(t["schema_version"], "0.1", f.name)
            self.assertLessEqual(set(t), intake.ROOT_KEYS, f.name)
            self.assertEqual(set(t["labels"]), intake.LABEL_KEYS, f.name)
            self.assertIn(t["split"], intake.SPLITS, f.name)
            self.assertTrue(t["events"], f.name)
            self.assertEqual([e["seq"] for e in t["events"]], list(range(len(t["events"]))), f.name)
            self.assertEqual(stub_tools.replay(t), [], f.name)

    def test_tool_schemas_use_only_intake_keywords(self) -> None:
        def walk(node: dict) -> None:
            self.assertLessEqual(set(node), stub_tools.INTAKE_SCHEMA_KEYWORDS)
            for sub in node.get("properties", {}).values():
                walk(sub)
            if "items" in node:
                walk(node["items"])

        for spec in stub_tools.tool_schema_list(sorted(stub_tools.TOOL_SCHEMAS)):
            walk(spec["parameters"])

    def test_unsafe_seed_is_labeled_fail_and_blocked_by_intake(self) -> None:
        doc = json.loads((FIXTURES / "seeds" / "unsafe-seed.json").read_text("utf-8"))
        seed = doc["tasks"][0]
        t = gen.build_trajectory(seed, 1, doc)
        self.assertEqual(t["labels"]["safety_gate"], "fail")
        self.assertEqual(t["labels"]["violation_code"], "S01,S02,S05")
        self.assertEqual(t["labels"]["task_success"], 0)
        self.assertTrue(t["notes"]["label_evidence"]["needs_human_review"])
        v = intake.Validator()
        v.validate_record(t, "unsafe")
        self.assertIn("unknown_tool", {f.code for f in v.findings}, "未授權工具在 v0.1 無法入庫")

    def test_correctly_labeled_failure_passes_both_checks(self) -> None:
        doc = json.loads((FIXTURES / "seeds" / "unsafe-seed.json").read_text("utf-8"))
        seed = copy.deepcopy(doc["tasks"][0])
        seed["steps"] = [s for s in seed["steps"] if s["tool"] != "web_fetch"]
        t = gen.build_trajectory(seed, 1, doc)
        self.assertEqual((t["labels"]["safety_gate"], t["labels"]["violation_code"]), ("fail", "S05"))
        v = intake.Validator()
        v.validate_record(t, "unsafe")
        self.assertEqual(v.findings, [])
        rep = audit.Report()
        audit.validate_trajectory(t, rep, "unsafe")
        self.assertEqual(rep.errors, [])

    def test_latency_thresholds_follow_rubric(self) -> None:
        cases = [(500, 4), (501, 3), (750, 3), (751, 2), (1000, 2), (1001, 1), (1500, 1), (1501, 0)]
        for elapsed, expect in cases:
            self.assertEqual(gen.latency_score(elapsed, 1000), expect, elapsed)
            self.assertEqual(audit._latency_label(elapsed, 1000), expect, elapsed)


class StubToolsTest(unittest.TestCase):
    def test_csv_transform_cleans_roc_dates_and_dupes(self) -> None:
        env = stub_tools.StubEnv(
            {"files": {"/sandbox/a.csv": "日期,值\n民國115年1月5日, x \n民國115年1月5日, x \n,\n"}}
        )
        env.call(
            "csv_transform",
            {
                "src": "/sandbox/a.csv",
                "dst": "/sandbox/b.csv",
                "operations": ["trim", "drop_empty_rows", "normalize_date", "dedupe"],
            },
        )
        self.assertEqual(env.files["/sandbox/b.csv"], "日期,值\n2026-01-05,x\n")

    def test_invalid_arguments_and_blocked_host(self) -> None:
        env = stub_tools.StubEnv({})
        _, err = stub_tools.execute(env, "fs_read", {"path": "/etc/passwd"})
        self.assertEqual(err and err["code"], "invalid_arguments")
        _, err = stub_tools.execute(env, "web_fetch", {"url": "https://www.google.com/"})
        self.assertEqual(err and err["code"], "blocked_host")

    def test_state_matches_semantics(self) -> None:
        actual = {"files": {"/a": "hello world"}, "deleted": []}
        self.assertEqual(stub_tools.state_matches({"files": {"/a": {"$contains": "world"}, "/b": None}}, actual)[1], [])
        self.assertTrue(stub_tools.state_matches({"files": {"/a": None}}, actual)[1])
        self.assertTrue(stub_tools.state_matches({"deleted": {"$len": 1}}, actual)[1])


class PositiveTest(unittest.TestCase):
    def test_intake_validator_cli_passes_generated_dataset(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_trajectory_dataset.py"), str(DATA_ROOT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("errors=0 warnings=0", proc.stdout)

    def test_audit_passes_generated_dataset_with_manifest(self) -> None:
        rep = audit.validate_paths([DATASET], MANIFEST)
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.gate, "PASS WITH NOTES")
        self.assertEqual(rep.checks["replay"], "RUN")
        self.assertEqual(rep.checks["manifest"], "RUN")
        self.assertEqual(rep.checks["human_review"], "NOT RUN")

    def test_valid_fixtures_pass_both_checks(self) -> None:
        self.assertEqual(_intake_codes(FIXTURES / "valid"), set())
        rep = audit.validate_paths([FIXTURES / "valid"])
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.count, 2)

    def test_audit_cli_exit_codes(self) -> None:
        self.assertEqual(audit.main([str(FIXTURES / "valid")]), 0)
        self.assertEqual(audit.main([str(FIXTURES / "invalid" / "invalid_locale__zh_cn.json")]), 1)
        self.assertEqual(audit.main([str(FIXTURES / "does-not-exist")]), 2)

    def test_placeholder_credentials_are_allowed(self) -> None:
        base = json.loads(next((FIXTURES / "valid").glob("*.json")).read_text("utf-8"))
        base["notes"]["final_answer"] += " token=PLACEHOLDER_TOKEN_DO_NOT_USE，聯絡 ops@example.com"
        rep = audit.Report()
        audit.validate_trajectory(base, rep, "placeholder")
        self.assertEqual(rep.errors, [])


class NegativeTest(unittest.TestCase):
    def test_each_invalid_fixture_is_blocked_with_expected_code(self) -> None:
        files = sorted((FIXTURES / "invalid").glob("*.json"))
        self.assertGreaterEqual(len(files), 30)
        for f in files:
            expected = f.name.split("__")[0]
            with self.subTest(fixture=f.name):
                if not expected.startswith("E_"):
                    self.assertIn(expected, _intake_codes(f), "入庫檢查器本身就要擋下")
                rep = audit.validate_paths([f])
                self.assertEqual(rep.gate, "BLOCKED")
                self.assertIn(expected, rep.codes())

    def test_audit_only_fixtures_pass_intake(self) -> None:
        for f in sorted((FIXTURES / "invalid").glob("E_*.json")):
            if f.name.startswith(("E_ARGS_SCHEMA", "E_EVENT_ERROR", "E_REPLAY")):
                continue
            with self.subTest(fixture=f.name):
                self.assertEqual(_intake_codes(f), set(), "這些案例是補充 audit 才擋得下的")

    def test_each_invalid_dataset_is_blocked_with_expected_code(self) -> None:
        dirs = sorted(p for p in (FIXTURES / "invalid_datasets").iterdir() if p.is_dir())
        self.assertGreaterEqual(len(dirs), 4)
        for d in dirs:
            manifest = d.parent / f"{d.name}.manifest.json"
            with self.subTest(dataset=d.name):
                rep = audit.validate_paths([d], manifest if manifest.exists() else None)
                self.assertEqual(rep.gate, "BLOCKED")
                self.assertIn(d.name, rep.codes())
                if not d.name.startswith("E_"):
                    self.assertIn(d.name, _intake_codes(d))

    def test_vendor_secret_patterns_are_detected(self) -> None:
        base = json.loads(next((FIXTURES / "valid").glob("*.json")).read_text("utf-8"))
        samples = [
            "sk-" + "A1b2C3d4" * 3,
            "AKIA" + "ABCDEFGHIJKLMNOP",
            "ghp_" + "x" * 36,
            "-----BEGIN RSA " + "PRIVATE KEY-----",
            "api_key=" + "q7w8e9r0t1y2u3",
            "身分證 A1" + "23456789",
            "手機 0912" + "345678",
        ]
        for s in samples:
            with self.subTest(sample=s[:8]):
                t = copy.deepcopy(base)
                t["notes"]["final_answer"] += s
                rep = audit.Report()
                audit.validate_trajectory(t, rep, "mut")
                self.assertTrue(rep.codes() & {"E_SECRET", "E_PII"})


if __name__ == "__main__":
    unittest.main()
