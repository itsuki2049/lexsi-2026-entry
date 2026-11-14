"""軌跡產生管線與入庫檢查的正負向測試。執行：python -m unittest discover -s tests -t ."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_trajectories as gen  # noqa: E402
import stub_tools  # noqa: E402
import validate_trajectory_dataset as val  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
DATASET = ROOT / "data" / "trajectories" / "zh-Hant"
SEEDS = ROOT / "data" / "seed-tasks-zh-Hant.json"


def _error_codes(rep: val.Report) -> set[str]:
    return rep.codes()


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
        self.assertTrue(any("expected_state" in e for e in gen.check_seed_doc(broken)))


class GeneratorTest(unittest.TestCase):
    def test_render_is_deterministic(self) -> None:
        raw = SEEDS.read_bytes()
        doc = json.loads(raw)
        self.assertEqual(gen.render(doc, raw), gen.render(json.loads(raw), raw))

    def test_committed_dataset_is_up_to_date(self) -> None:
        self.assertEqual(gen.main(["--check"]), 0)

    def test_every_trajectory_has_required_v0_fields_and_replays(self) -> None:
        files = sorted(DATASET.glob("traj-*.json"))
        self.assertGreaterEqual(len(files), 20)
        for f in files:
            t = json.loads(f.read_text("utf-8"))
            for key in ("id", "locale", "task", "tool_schemas", "events", "final_state", "budget_ms", "labels"):
                self.assertIn(key, t, f.name)
            self.assertTrue(t["events"], f.name)
            self.assertEqual(stub_tools.replay(t), [], f.name)

    def test_unsafe_seed_is_labeled_fail(self) -> None:
        doc = json.loads((FIXTURES / "seeds" / "unsafe-seed.json").read_text("utf-8"))
        t = gen.build_trajectory(doc["tasks"][0], 1, doc)
        gate = t["labels"]["safety_gate"]
        self.assertEqual(gate["status"], "fail")
        self.assertEqual({v["code"] for v in gate["violations"]}, {"S01", "S02", "S05"})
        self.assertEqual(t["labels"]["task_success"], 0)
        self.assertEqual(t["labels"]["tool_call_accuracy"], 0)
        self.assertTrue(t["labels"]["needs_human_review"])
        rep = val.Report()
        val.validate_trajectory(t, rep, "unsafe")
        self.assertEqual(rep.errors, [], "標記正確的失敗軌跡應能入庫")

    def test_latency_thresholds_follow_rubric(self) -> None:
        cases = [(500, 4), (501, 3), (750, 3), (751, 2), (1000, 2), (1001, 1), (1500, 1), (1501, 0)]
        for elapsed, expect in cases:
            self.assertEqual(gen.latency_score(elapsed, 1000), expect, elapsed)
            self.assertEqual(val._latency_label(elapsed, 1000), expect, elapsed)


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


class ValidatorPositiveTest(unittest.TestCase):
    def test_generated_dataset_passes(self) -> None:
        rep = val.validate_paths([DATASET])
        self.assertEqual(rep.errors, [])
        self.assertNotEqual(rep.gate, "BLOCKED")
        self.assertEqual(rep.checks["replay"], "RUN")
        self.assertEqual(rep.checks["manifest"], "RUN")
        self.assertEqual(rep.checks["human_review"], "NOT RUN")

    def test_valid_fixtures_pass(self) -> None:
        rep = val.validate_paths([FIXTURES / "valid"])
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.count, 2)

    def test_cli_exit_codes(self) -> None:
        self.assertEqual(val.main([str(FIXTURES / "valid")]), 0)
        self.assertEqual(val.main([str(FIXTURES / "invalid" / "E_LOCALE__zh_cn.json")]), 1)
        self.assertEqual(val.main([str(FIXTURES / "does-not-exist")]), 2)


class ValidatorNegativeTest(unittest.TestCase):
    def test_each_invalid_fixture_is_blocked_with_expected_code(self) -> None:
        files = sorted((FIXTURES / "invalid").glob("*.json"))
        self.assertGreaterEqual(len(files), 15)
        for f in files:
            expected = f.name.split("__")[0]
            with self.subTest(fixture=f.name):
                rep = val.validate_paths([f])
                self.assertEqual(rep.gate, "BLOCKED")
                self.assertIn(expected, _error_codes(rep))

    def test_each_invalid_dataset_is_blocked_with_expected_code(self) -> None:
        dirs = sorted(p for p in (FIXTURES / "invalid_datasets").iterdir() if p.is_dir())
        self.assertGreaterEqual(len(dirs), 4)
        for d in dirs:
            with self.subTest(dataset=d.name):
                rep = val.validate_paths([d])
                self.assertEqual(rep.gate, "BLOCKED")
                self.assertIn(d.name, _error_codes(rep))

    def test_vendor_secret_patterns_are_detected(self) -> None:
        base = json.loads((FIXTURES / "valid" / "traj-20261231-901.json").read_text("utf-8"))
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
                t["final_answer"] += s
                rep = val.Report()
                val.validate_trajectory(t, rep, "mut")
                self.assertTrue(_error_codes(rep) & {"E_SECRET", "E_PII"})

    def test_placeholder_credentials_are_allowed(self) -> None:
        base = json.loads((FIXTURES / "valid" / "traj-20261231-901.json").read_text("utf-8"))
        base["final_answer"] += " token=PLACEHOLDER_TOKEN_DO_NOT_USE，聯絡 ops@example.com"
        rep = val.Report()
        val.validate_trajectory(base, rep, "placeholder")
        self.assertEqual(rep.errors, [])


if __name__ == "__main__":
    unittest.main()
