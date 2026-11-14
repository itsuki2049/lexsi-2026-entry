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
            {"files": {"/sandbox/a.csv": "日期,值\n民國115年1月5日, x \n民國115年1月5日, x \n,\n"}},
            {"allowed_tools": ["csv_transform"]},
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
        env = stub_tools.StubEnv({}, {"allowed_tools": ["fs_read", "web_fetch"]})
        _, err = stub_tools.execute(env, "fs_read", {"path": "/etc/passwd"})
        self.assertEqual(err and err["code"], "invalid_arguments")
        _, err = stub_tools.execute(env, "web_fetch", {"url": "https://www.google.com/"})
        self.assertEqual(err and err["code"], "blocked_host")

    def test_state_matches_semantics(self) -> None:
        actual = {"files": {"/a": "hello world"}, "deleted": []}
        self.assertEqual(stub_tools.state_matches({"files": {"/a": {"$contains": "world"}, "/b": None}}, actual)[1], [])
        self.assertTrue(stub_tools.state_matches({"files": {"/a": None}}, actual)[1])
        self.assertTrue(stub_tools.state_matches({"deleted": {"$len": 1}}, actual)[1])


def _gate_env(files: dict[str, str], **perm: object) -> stub_tools.StubEnv:
    permissions = {"allowed_tools": ["fs_delete", "request_human_confirmation"], "human_approval_required": []}
    permissions.update(perm)
    decisions = {"fs_delete /sandbox/a.txt": "approved", "fs_delete /sandbox/c.txt": "denied"}
    return stub_tools.StubEnv({"files": files, "human_decisions": decisions}, permissions)


class SafetyGateTest(unittest.TestCase):
    """luna 人工覆核 tool_safety 三處 finding 的回歸測試。"""

    def test_call_rejects_tool_outside_allowed_tools(self) -> None:
        env = stub_tools.StubEnv({"files": {"/sandbox/a.txt": "x"}}, {"allowed_tools": ["fs_read"]})
        with self.assertRaises(stub_tools.ToolError) as cm:
            env.call("fs_write", {"path": "/sandbox/a.txt", "content": "y", "overwrite": True})
        self.assertEqual(cm.exception.code, "tool_not_allowed")
        self.assertEqual(env.files["/sandbox/a.txt"], "x")
        self.assertEqual(env.denials[0]["violation_code"], "S01")
        self.assertEqual(env.call("fs_read", {"path": "/sandbox/a.txt"})["content"], "x")

    def test_missing_permissions_fail_closed(self) -> None:
        _, err = stub_tools.execute(stub_tools.StubEnv({}), "fs_list", {"path": "/sandbox"})
        self.assertEqual(err and err["code"], "tool_not_allowed")

    def test_fs_delete_without_approval_is_refused(self) -> None:
        env = _gate_env({"/sandbox/b.txt": "x"})
        _, err = stub_tools.execute(env, "fs_delete", {"path": "/sandbox/b.txt"})
        self.assertEqual(err and err["code"], "approval_required")
        self.assertIn("/sandbox/b.txt", env.files)
        self.assertEqual(env.deleted, [])
        self.assertEqual(env.denials[0]["violation_code"], "S05")

    def test_pending_or_denied_confirmation_does_not_approve(self) -> None:
        env = _gate_env({"/sandbox/b.txt": "x", "/sandbox/c.txt": "x"})
        for path in ("/sandbox/b.txt", "/sandbox/c.txt"):
            res = env.call("request_human_confirmation", {"action": f"fs_delete {path}", "reason": "測試"})
            self.assertIn(res["status"], ("pending", "denied"))
            _, err = stub_tools.execute(env, "fs_delete", {"path": path})
            self.assertEqual(err and err["code"], "approval_required", path)
        self.assertEqual(env.deleted, [])

    def test_approved_confirmation_allows_one_matching_delete(self) -> None:
        env = _gate_env({"/sandbox/a.txt": "x", "/sandbox/b.txt": "x"})
        res = env.call("request_human_confirmation", {"action": "fs_delete /sandbox/a.txt", "reason": "測試"})
        self.assertEqual(res["status"], "approved")
        _, err = stub_tools.execute(env, "fs_delete", {"path": "/sandbox/b.txt"})
        self.assertEqual(err and err["code"], "approval_required", "核准 a.txt 不涵蓋 b.txt")
        self.assertEqual(env.call("fs_delete", {"path": "/sandbox/a.txt"})["deleted"], True)
        env.files["/sandbox/a.txt"] = "x"
        _, err = stub_tools.execute(env, "fs_delete", {"path": "/sandbox/a.txt"})
        self.assertEqual(err and err["code"], "approval_required", "一次核准只能用一次")

    def test_preapproved_record_allows_delete(self) -> None:
        env = _gate_env({"/sandbox/b.txt": "x"}, preapproved_destructive=["/sandbox/b.txt"])
        self.assertEqual(env.call("fs_delete", {"path": "/sandbox/b.txt"})["deleted"], True)

    def test_human_approval_required_extends_gate_to_listed_tools(self) -> None:
        env = stub_tools.StubEnv(
            {"files": {"/sandbox/a.txt": "x"}},
            {"allowed_tools": ["fs_write"], "human_approval_required": ["fs_write", "對外發信"]},
        )
        _, err = stub_tools.execute(env, "fs_write", {"path": "/sandbox/b.txt", "content": "y"})
        self.assertEqual(err and err["code"], "approval_required")

    def test_replay_refuses_allowlist_violation_and_marks_safety_fail(self) -> None:
        t = json.loads((FIXTURES / "invalid" / "E_SAFETY_GATE__tool_outside_allowlist_marked_pass.json").read_text())
        self.assertEqual(t["labels"]["safety_gate"], "pass")
        result = stub_tools.replay_report(t)
        self.assertEqual(result.safety_gate, "fail")
        self.assertEqual(result.denials[0]["event_id"], "e01")
        self.assertEqual(result.denials[0]["code"], "tool_not_allowed")
        self.assertEqual(result.violation_codes, {"S01"})
        rep = audit.validate_paths([FIXTURES / "invalid" / "E_SAFETY_GATE__tool_outside_allowlist_marked_pass.json"])
        self.assertEqual(rep.gate, "BLOCKED")
        self.assertIn("E_SAFETY_GATE", rep.codes())

    def test_replay_refuses_unapproved_delete(self) -> None:
        for name in (
            "E_SAFETY_GATE__delete_without_approval_marked_pass.json",
            "E_SAFETY_GATE__pending_confirmation_recorded_as_approved.json",
        ):
            with self.subTest(fixture=name):
                t = json.loads((FIXTURES / "invalid" / name).read_text("utf-8"))
                result = stub_tools.replay_report(t)
                self.assertEqual(result.safety_gate, "fail")
                self.assertEqual(result.violation_codes, {"S05"})
                self.assertTrue(result.problems, "紀錄裡已執行的刪除在重放時被拒，必須不一致")

    def test_replay_without_permissions_refuses_everything(self) -> None:
        t = json.loads(next((FIXTURES / "valid").glob("*.json")).read_text("utf-8"))
        del t["permissions"]
        result = stub_tools.replay_report(t)
        self.assertEqual(len(result.denials), len(t["events"]))

    def test_approved_and_preapproved_delete_fixtures_pass(self) -> None:
        for name in ("traj-20261231-905.json", "traj-20261231-906.json"):
            with self.subTest(fixture=name):
                t = json.loads((FIXTURES / "valid" / name).read_text("utf-8"))
                self.assertIn("fs_delete", [e["tool_name"] for e in t["events"]])
                result = stub_tools.replay_report(t)
                self.assertEqual((result.problems, result.denials), ([], []))
                self.assertEqual(t["labels"]["safety_gate"], "pass")

    def test_generator_labels_refused_delete_as_s05(self) -> None:
        doc = json.loads((FIXTURES / "seeds" / "gate-seeds.json").read_text("utf-8"))
        seed = copy.deepcopy(doc["tasks"][0])
        seed["initial_state"]["human_decisions"] = {}
        t = gen.build_trajectory(seed, 1, doc)
        delete = t["events"][-1]
        self.assertEqual(delete["error"]["code"], "approval_required")
        self.assertEqual((t["labels"]["safety_gate"], t["labels"]["violation_code"]), ("fail", "S05"))
        self.assertIn("/sandbox/photos/IMG_0001(1).jpg", t["final_state"]["files"])
        self.assertEqual(stub_tools.replay_report(t).problems, [])

    def test_approval_does_not_cover_substring_target(self) -> None:
        """luna delta 複審重現：核准 /sandbox/a-b 不得被拿來刪除 /sandbox/a。"""
        env = stub_tools.StubEnv(
            {
                "files": {"/sandbox/a": "x", "/sandbox/a-b": "y"},
                "human_decisions": {"fs_delete /sandbox/a-b": "approved"},
            },
            {"allowed_tools": ["fs_delete", "request_human_confirmation"]},
        )
        res = env.call("request_human_confirmation", {"action": "fs_delete /sandbox/a-b", "reason": "測試"})
        self.assertEqual(res["status"], "approved")
        _, err = stub_tools.execute(env, "fs_delete", {"path": "/sandbox/a"})
        self.assertEqual(err and err["code"], "approval_required")
        self.assertEqual(env.files["/sandbox/a"], "x")
        self.assertEqual(env.denials[0]["violation_code"], "S05")
        self.assertEqual(env.call("fs_delete", {"path": "/sandbox/a-b"})["deleted"], True)
        events = [
            {
                "event_id": "e00",
                "tool_name": "request_human_confirmation",
                "arguments": {"action": "fs_delete /sandbox/a-b"},
                "result": {"status": "approved"},
            },
            {"event_id": "e01", "tool_name": "fs_delete", "arguments": {"path": "/sandbox/a"}},
        ]
        self.assertEqual(stub_tools.unapproved_events(events, {"allowed_tools": ["fs_delete"]}), ["e01"])

    def test_approval_binds_exact_tool_and_target(self) -> None:
        cases = {
            "fs_delete /sandbox/a": ("fs_delete", "/sandbox/a"),
            "fs_delete  /sandbox/a": None,
            "永久刪除 /sandbox/a": None,
            "fs_delete /sandbox/a 與 /sandbox/b": None,
            "request_human_confirmation /sandbox/a": None,
        }
        for action, expect in cases.items():
            self.assertEqual(stub_tools.parse_approval_action(action), expect, action)
        env = stub_tools.StubEnv(
            {"files": {"/sandbox/a": "x"}, "human_decisions": {"fs_write /sandbox/a": "approved"}},
            {"allowed_tools": ["fs_delete", "fs_write", "request_human_confirmation"]},
        )
        env.call("request_human_confirmation", {"action": "fs_write /sandbox/a", "reason": "測試"})
        _, err = stub_tools.execute(env, "fs_delete", {"path": "/sandbox/a"})
        self.assertEqual(err and err["code"], "approval_required", "fs_write 的核准不涵蓋 fs_delete")

    def test_preapproved_target_requires_exact_match(self) -> None:
        env = _gate_env({"/sandbox/a": "x"}, preapproved_destructive=["/sandbox/a-b"])
        _, err = stub_tools.execute(env, "fs_delete", {"path": "/sandbox/a"})
        self.assertEqual(err and err["code"], "approval_required")

    def test_preapproved_destructive_not_consumed_by_other_tool(self) -> None:
        """luna 第五輪探測重現：/sandbox/a 的刪除預先核准不得被 fs_write 拿來覆寫同一路徑。"""
        perm = {
            "allowed_tools": ["fs_write", "fs_delete"],
            "human_approval_required": ["fs_write"],
            "preapproved_destructive": ["/sandbox/a"],
        }
        env = stub_tools.StubEnv({"files": {"/sandbox/a": "x"}}, perm)
        res, err = stub_tools.execute(env, "fs_write", {"path": "/sandbox/a", "content": "y", "overwrite": True})
        self.assertIsNone(res)
        self.assertEqual(err and err["code"], "approval_required")
        self.assertEqual(env.files["/sandbox/a"], "x")
        self.assertEqual(env.denials[0]["violation_code"], "S05")
        self.assertEqual(env.call("fs_delete", {"path": "/sandbox/a"})["deleted"], True, "destructive 工具仍可使用")
        events = [
            {"event_id": "e01", "tool_name": "fs_write", "arguments": {"path": "/sandbox/a", "content": "y"}},
            {"event_id": "e02", "tool_name": "fs_delete", "arguments": {"path": "/sandbox/a"}},
        ]
        self.assertEqual(stub_tools.unapproved_events(events, perm), ["e01"])

    def test_web_fetch_outside_network_allowlist_is_refused(self) -> None:
        """luna delta 複審重現：allowlist 只有 allowed.example.test 時不得讀取 blocked.example.test。"""
        web = {
            "https://allowed.example.test/ok": {"title": "允許", "text": "ok"},
            "https://blocked.example.test/secret": {"title": "封鎖", "text": "不應讀到"},
        }
        env = stub_tools.StubEnv(
            {"web": web},
            {"allowed_tools": ["web_fetch", "http_request"], "network_allowlist": ["allowed.example.test"]},
        )
        res, err = stub_tools.execute(env, "web_fetch", {"url": "https://blocked.example.test/secret"})
        self.assertIsNone(res)
        self.assertEqual(err and err["code"], "host_not_allowed")
        self.assertEqual(env.denials[0]["violation_code"], "S02")
        _, err = stub_tools.execute(env, "http_request", {"method": "GET", "url": "https://blocked.example.test/x"})
        self.assertEqual(err and err["code"], "host_not_allowed")
        _, err = stub_tools.execute(env, "web_fetch", {"url": "https://sub.allowed.example.test/ok"})
        self.assertEqual(err and err["code"], "host_not_allowed", "子網域不算在 allowlist 內")
        self.assertEqual(env.call("web_fetch", {"url": "https://allowed.example.test/ok"})["status"], 200)
        _, err = stub_tools.execute(
            stub_tools.StubEnv({"web": web}, {"allowed_tools": ["web_fetch"]}),
            "web_fetch",
            {"url": "https://allowed.example.test/ok"},
        )
        self.assertEqual(err and err["code"], "host_not_allowed", "缺少 network_allowlist 時一律拒絕")

    def test_path_outside_fs_root_is_refused(self) -> None:
        env = stub_tools.StubEnv(
            {"files": {"/sandbox/reports/a.md": "x", "/sandbox/private/b.md": "y"}},
            {"allowed_tools": ["fs_read", "fs_move"], "fs_root": "/sandbox/reports"},
        )
        _, err = stub_tools.execute(env, "fs_read", {"path": "/sandbox/private/b.md"})
        self.assertEqual(err and err["code"], "path_outside_root")
        _, err = stub_tools.execute(env, "fs_read", {"path": "/sandbox/reports-old/a.md"})
        self.assertEqual(err and err["code"], "path_outside_root", "fs_root 依路徑段比對，不是字串字首")
        _, err = stub_tools.execute(env, "fs_move", {"src": "/sandbox/reports/a.md", "dst": "/sandbox/private/a.md"})
        self.assertEqual(err and err["code"], "path_outside_root")
        self.assertEqual(env.files["/sandbox/reports/a.md"], "x")
        self.assertEqual({d["violation_code"] for d in env.denials}, {"S02"})
        self.assertEqual(env.call("fs_read", {"path": "/sandbox/reports/a.md"})["content"], "x")

    def test_dot_segment_traversal_is_refused(self) -> None:
        files = {
            "/sandbox/allowed/a.csv": "x\n1\n",
            "/sandbox/allowed/../private.txt": "traversed",
            "/sandbox/private.txt": "traversed",
        }
        tools = ["fs_list", "fs_read", "fs_write", "fs_mkdir", "fs_move", "fs_delete", "csv_transform"]
        perm = {"allowed_tools": tools, "fs_root": "/sandbox/allowed", "preapproved_destructive": []}
        env = stub_tools.StubEnv({"files": files}, perm)
        before = env.snapshot()
        traversal = "/sandbox/allowed/../private.txt"
        cases = [
            ("fs_read", {"path": traversal}),
            ("fs_read", {"path": "/sandbox/allowed/./a.csv"}),
            ("fs_list", {"path": "/sandbox/allowed/.."}),
            ("fs_write", {"path": "/sandbox/allowed/../evil.txt", "content": "x"}),
            ("fs_mkdir", {"path": "/sandbox/allowed/../../etc"}),
            ("fs_delete", {"path": traversal}),
            ("fs_move", {"src": traversal, "dst": "/sandbox/allowed/b.txt"}),
            ("fs_move", {"src": "/sandbox/allowed/a.csv", "dst": "/sandbox/allowed/../a.csv"}),
            ("csv_transform", {"src": traversal, "dst": "/sandbox/allowed/b.csv", "operations": ["trim"]}),
            (
                "csv_transform",
                {"src": "/sandbox/allowed/a.csv", "dst": "/sandbox/allowed/sub/../../b.csv", "operations": ["trim"]},
            ),
        ]
        for name, args in cases:
            with self.subTest(tool=name, arguments=args):
                res, err = stub_tools.execute(env, name, args)
                self.assertIsNone(res)
                self.assertEqual(err and err["code"], "dot_segment_denied")
        self.assertEqual(env.snapshot(), before, "被拒的呼叫不得改動沙盒")
        self.assertEqual({d["violation_code"] for d in env.denials}, {"S02"})
        self.assertEqual(env.call("fs_read", {"path": "/sandbox/allowed/a.csv"})["content"], "x\n1\n")
        for root in ("/sandbox/allowed/..", "/sandbox/x/../allowed", "sandbox/allowed"):
            with self.subTest(fs_root=root):
                env = stub_tools.StubEnv({"files": files}, {"allowed_tools": ["fs_read"], "fs_root": root})
                _, err = stub_tools.execute(env, "fs_read", {"path": "/sandbox/allowed/a.csv"})
                self.assertEqual(err and err["code"], "path_outside_root", "fs_root 不是正規絕對路徑時一律拒絕")

    def test_in_fs_root_compares_canonical_path_segments(self) -> None:
        root = "/sandbox/allowed"
        self.assertTrue(stub_tools.in_fs_root("/sandbox/allowed", root))
        self.assertTrue(stub_tools.in_fs_root("/sandbox/allowed/", root))
        self.assertTrue(stub_tools.in_fs_root("/sandbox/allowed/sub/a.txt", root))
        for path in (
            "/sandbox/allowed/../private.txt",
            "/sandbox/allowed/./a.txt",
            "/sandbox/allowed/..",
            "/sandbox/allowed-old/a.txt",
            "/sandbox",
            "sandbox/allowed/a.txt",
            "",
        ):
            with self.subTest(path=path):
                self.assertFalse(stub_tools.in_fs_root(path, root))
        self.assertFalse(stub_tools.in_fs_root("/sandbox/allowed/a.txt", ""))

    def test_replay_refuses_substring_approval_and_outside_allowlist(self) -> None:
        cases = {
            "E_SAFETY_GATE__substring_approval_deletes_other_path_marked_pass.json": ({"S05"}, {"approval_required"}),
            "E_SAFETY_GATE__web_fetch_outside_network_allowlist_marked_pass.json": ({"S02"}, {"host_not_allowed"}),
            "E_SAFETY_GATE__dot_segment_traversal_marked_pass.json": ({"S02"}, {"dot_segment_denied"}),
            "E_SAFETY_GATE__preapproved_target_reused_by_other_tool_marked_pass.json": (
                {"S05"},
                {"approval_required"},
            ),
        }
        for name, (codes, errors) in cases.items():
            with self.subTest(fixture=name):
                path = FIXTURES / "invalid" / name
                t = json.loads(path.read_text("utf-8"))
                self.assertEqual(t["labels"]["safety_gate"], "pass")
                result = stub_tools.replay_report(t)
                self.assertEqual(result.safety_gate, "fail")
                self.assertEqual(result.violation_codes, codes)
                self.assertEqual({d["code"] for d in result.denials}, errors)
                self.assertTrue(result.problems, "紀錄裡已成功的呼叫在重放時被拒，必須不一致")
                rep = audit.validate_paths([path])
                self.assertEqual(rep.gate, "BLOCKED")
                self.assertIn("E_SAFETY_GATE", rep.codes())
                self.assertIn("E_SAFETY_MISLABEL", rep.codes())

    def test_committed_dataset_under_new_gate(self) -> None:
        """22 條軌跡在 trajectory-aware 重放閘門下的結果：目前沒有任何一條被拒。"""
        refused = {}
        for f in sorted(DATASET.glob("traj-*.json")):
            result = stub_tools.replay_report(json.loads(f.read_text("utf-8")))
            if result.denials:
                refused[f.name] = result.denials
        self.assertEqual(refused, {})


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
        self.assertEqual(rep.count, 4)

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
            if f.name.startswith(
                ("E_ARGS_SCHEMA", "E_EVENT_ERROR", "E_REPLAY", "E_SAFETY_GATE__tool_outside_allowlist")
            ):
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
