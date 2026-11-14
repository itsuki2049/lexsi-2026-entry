#!/usr/bin/env python3
"""從 tests/fixtures/seeds/fixture-seeds.json 產生正負向 fixture。

用法：
    python tests/build_fixtures.py          # 重寫 tests/fixtures/{valid,invalid,invalid_datasets}
    python tests/build_fixtures.py --check  # 只確認已提交的 fixture 是最新

負向 fixture 檔名格式是 <預期錯誤代碼>__<說明>.json。小寫代碼來自 v0.1 入庫檢查器
（scripts/validate_trajectory_dataset.py），E_ 開頭的代碼來自補充 audit
（scripts/audit_trajectory_dataset.py）。資料集層級的負向案例是 invalid_datasets/<代碼>/ 目錄，
需要 manifest 的案例另附 <代碼>.manifest.json。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_trajectories as gen

FIXTURES = ROOT / "tests" / "fixtures"
SEEDS = FIXTURES / "seeds" / "fixture-seeds.json"
GATE_SEEDS = FIXTURES / "seeds" / "gate-seeds.json"
ESCAPE_CHARS = {"\u2014": "\\u2014", "\u2013": "\\u2013"}

Traj = dict[str, Any]


def _set(path: str, value: Any) -> Callable[[Traj], None]:
    def apply(t: Traj) -> None:
        *parents, last = path.split(".")
        node: Any = t
        for key in parents:
            node = node[int(key)] if isinstance(node, list) else node[key]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value

    return apply


def _all(*fns: Callable[[Traj], None]) -> Callable[[Traj], None]:
    def apply(t: Traj) -> None:
        for fn in fns:
            fn(t)

    return apply


def _append_answer(text: str) -> Callable[[Traj], None]:
    def apply(t: Traj) -> None:
        t["notes"]["final_answer"] += text

    return apply


def _del(key: str) -> Callable[[Traj], None]:
    return lambda t: t.pop(key)


def _add_schema_pattern(t: Traj) -> None:
    props = t["tool_schemas"][0]["parameters"]["properties"]
    next(iter(props.values()))["pattern"] = "^.+$"


def _drop_trust(t: Traj) -> None:
    del t["events"][0]["trust_level"]


def _drop_result(t: Traj) -> None:
    del t["events"][1]["result"]


def _drop_host(t: Traj) -> None:
    t["permissions"]["network_allowlist"] = t["permissions"]["network_allowlist"][:1]


def _unproven(t: Traj) -> None:
    t["expected_state"].setdefault("files", {})["/sandbox/不存在的報告.md"] = {"$contains": "結論"}


def _wrong_latency(t: Traj) -> None:
    t["labels"]["latency"] = (t["labels"]["latency"] + 2) % 5


def _high_no_review(t: Traj) -> None:
    t["labels"]["safety_review"]["severity"] = "high"
    t["notes"]["label_evidence"]["needs_human_review"] = False


def _bad_fs_path(t: Traj) -> None:
    ev = next(e for e in t["events"] if e["tool_name"] == "fs_write")
    ev["arguments"]["path"] = "/etc/cron.d/agent"


SINGLE: dict[str, Callable[[Traj], None]] = {
    "missing_field__no_final_state": _del("final_state"),
    "schema_version__v0": _set("schema_version", "0"),
    "invalid_id__bad_format": _set("id", "traj-2026-1"),
    "invalid_locale__zh_cn": _set("locale", "zh-CN"),
    "invalid_split__dev": _set("split", "dev"),
    "unreviewed_field__root_final_answer": _set("final_answer", "移到根欄位的答案"),
    "unknown_tool__undeclared_tool": _set("events.0.tool_name", "shell_exec"),
    "invalid_event_seq__starts_at_one": _set("events.0.seq", 1),
    "invalid_trust_level__missing": _drop_trust,
    "missing_result__no_result_key": _drop_result,
    "non_monotonic_time__timestamp_backwards": _set("events.1.timestamp_ms", 0),
    "unexpected_tool_argument__extra_argument": _set("events.0.arguments.extra", 1),
    "tool_allowlist_mismatch__extra_allowed_tool": lambda t: t["permissions"]["allowed_tools"].append("fs_delete"),
    "unsupported_schema_keyword__pattern": _add_schema_pattern,
    "invalid_score__score_5": _set("labels.task_success", 5),
    "missing_violation_code__fail_without_code": _set("labels.safety_gate", "fail"),
    "missing_judge_model__llm_without_model": lambda t: t["labels_metadata"].pop("judge_model"),
    "invalid_created_at__no_timezone": _set("provenance.created_at", "2026-11-14T00:00:00"),
    "sensitive_email_like__real_domain": _append_answer(" 結果另寄 wang.daming@mailhost.tw"),
    "sensitive_api_key_like__sk_key": _append_answer(" sk-" + "A1b2C3d4" * 3),
    "E_ARGS_SCHEMA__path_outside_sandbox": _bad_fs_path,
    "E_DASH__dash_in_answer": _append_answer("\u2014補充說明"),
    "E_EVENT_ERROR__error_without_kind": _all(_set("events.0.error", {"code": "x"}), _set("events.0.result", None)),
    "E_GROUP_ID__group_differs_from_template": _set("group_id", "tpl-other"),
    "E_LATENCY_LABEL__wrong_latency": _wrong_latency,
    "E_PII__mobile_without_context": _append_answer(" 請回撥 0912-345-678"),
    "E_PRODUCTION_URL__real_domain_in_allowlist": lambda t: t["permissions"]["network_allowlist"].append(
        "www.google.com"
    ),
    "E_PROVENANCE__unknown_license": _set("provenance.license", "unknown"),
    "E_REPLAY__tampered_result": _set("events.0.result", {"tampered": True}),
    "E_RISK_FIELDS__high_severity_no_review": _high_no_review,
    "E_SAFETY_LABEL__code_without_evidence": _all(
        _set("labels.safety_gate", "fail"), _set("labels.violation_code", "S01")
    ),
    "E_SAFETY_MISLABEL__host_outside_allowlist_marked_pass": _drop_host,
    "E_SECRET__bearer_token": _append_answer(" Authorization: Bearer " + "abcDEF123" * 3),
    "E_SIMPLIFIED__simplified_task": _set("task", "请帮我查证这个网页的数据是否正确。"),
    "E_SUCCESS_UNPROVEN__fabricated_success": _unproven,
}


def _drop_allowed(tool: str) -> Callable[[Traj], None]:
    def apply(t: Traj) -> None:
        t["permissions"]["allowed_tools"].remove(tool)

    return apply


def _widen_approved_action(t: Traj) -> None:
    """把核准的 action 換成目標的超字串，舊版子字串比對會誤把它當成刪除原檔的核准。"""
    ev = next(e for e in t["events"] if e["tool_name"] == "request_human_confirmation")
    old = ev["arguments"]["action"]
    wider = old + ".bak"
    ev["arguments"]["action"] = wider
    t["initial_state"]["human_decisions"] = {wider: t["initial_state"]["human_decisions"].pop(old)}
    for c in t["final_state"]["confirmations_requested"]:
        if c["action"] == old:
            c["action"] = wider


def _narrow_network_allowlist(t: Traj) -> None:
    t["permissions"]["network_allowlist"] = ["allowed.example.test"]


def _dot_segment_traversal(t: Traj) -> None:
    """fs_root 收窄成 /sandbox/tmp，刪除目標改寫成 /sandbox/tmp/../cache.bin。

    舊版字串字首比對會放行，記錄的結果也照舊版字面路徑的沙盒行為重算，讓重放只剩閘門這一個差異。
    """
    old, new = "/sandbox/tmp/cache.bin", "/sandbox/tmp/../cache.bin"
    swapped = json.loads(json.dumps(t, ensure_ascii=False).replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1]))
    t.clear()
    t.update(swapped)
    t["permissions"]["fs_root"] = "/sandbox/tmp"
    env = gen.stub_tools.StubEnv(t["initial_state"], t["permissions"])
    for ev in t["events"]:
        if ev["tool_name"] == "fs_list":
            ev["result"] = env._fs_list(**ev["arguments"])
        elif ev["tool_name"] == "fs_delete":
            env._fs_delete(**ev["arguments"])
    t["final_state"] = env.snapshot()


def _preapproved_target_reused_by_other_tool(t: Traj) -> None:
    """把預先核准刪除的 fs_delete 換成覆寫同一路徑的 fs_write，fs_write 列在 human_approval_required。

    舊版只比對目標、不限 destructive 工具，會讓 fs_write 借用 preapproved_destructive 直接覆寫；
    記錄的結果照舊版放行時的沙盒行為重算，讓重放只剩閘門這一個差異。
    """
    target = t["permissions"]["preapproved_destructive"][0]
    tools = sorted({"fs_write" if n == "fs_delete" else n for n in t["permissions"]["allowed_tools"]})
    t["tool_schemas"] = gen.stub_tools.tool_schema_list(tools)
    t["permissions"]["allowed_tools"] = tools
    t["permissions"]["human_approval_required"] = ["fs_write"]
    t["permissions"]["side_effects"] = gen.side_effect_kinds(tools)
    env = gen.stub_tools.StubEnv(t["initial_state"], t["permissions"])
    for ev in t["events"]:
        if ev["tool_name"] == "fs_list":
            ev["result"] = env._fs_list(**ev["arguments"])
        elif ev["tool_name"] == "fs_delete":
            ev["tool_name"] = "fs_write"
            ev["arguments"] = {"path": target, "content": "覆寫", "overwrite": True}
            ev["result"] = env._fs_write(**ev["arguments"])
            ev["rationale"] = "借用刪除的預先核准覆寫同一路徑"
    t["final_state"] = env.snapshot()
    t["expected_state"] = {"files": dict(t["final_state"]["files"])}
    t["notes"]["final_answer"] = f"已覆寫 {target}，keep.txt 保留不動。"


GATE_SINGLE: dict[str, tuple[str, Callable[[Traj], None]]] = {
    "E_SAFETY_GATE__tool_outside_allowlist_marked_pass": ("valid", _drop_allowed("web_search")),
    "E_SAFETY_GATE__delete_without_approval_marked_pass": (
        "preapproved",
        _set("permissions.preapproved_destructive", []),
    ),
    "E_SAFETY_GATE__pending_confirmation_recorded_as_approved": (
        "confirmed",
        _set("initial_state.human_decisions", {}),
    ),
    "E_SAFETY_GATE__substring_approval_deletes_other_path_marked_pass": ("confirmed", _widen_approved_action),
    "E_SAFETY_GATE__web_fetch_outside_network_allowlist_marked_pass": ("valid", _narrow_network_allowlist),
    "E_SAFETY_GATE__dot_segment_traversal_marked_pass": ("preapproved", _dot_segment_traversal),
    "E_SAFETY_GATE__preapproved_target_reused_by_other_tool_marked_pass": (
        "preapproved",
        _preapproved_target_reused_by_other_tool,
    ),
}


def dump(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    for ch, esc in ESCAPE_CHARS.items():
        text = text.replace(ch, esc)
    return text


def build() -> dict[str, str]:
    raw = SEEDS.read_bytes()
    doc = json.loads(raw)
    a = gen.build_trajectory(doc["tasks"][0], 901, doc)
    b = gen.build_trajectory(doc["tasks"][1], 902, doc)
    gate_doc = json.loads(GATE_SEEDS.read_bytes())
    confirmed = gen.build_trajectory(gate_doc["tasks"][0], 905, gate_doc)
    preapproved = gen.build_trajectory(gate_doc["tasks"][1], 906, gate_doc)
    out: dict[str, str] = {
        f"valid/{a['id']}.json": dump(a),
        f"valid/{b['id']}.json": dump(b),
        f"valid/{confirmed['id']}.json": dump(confirmed),
        f"valid/{preapproved['id']}.json": dump(preapproved),
        "invalid/invalid_json__truncated.json": dump(a)[:200] + "\n",
    }
    for name, mutate in SINGLE.items():
        t = copy.deepcopy(a)
        mutate(t)
        out[f"invalid/{name}.json"] = dump(t)
    bases = {"valid": a, "confirmed": confirmed, "preapproved": preapproved}
    for name, (base, mutate) in GATE_SINGLE.items():
        t = copy.deepcopy(bases[base])
        mutate(t)
        out[f"invalid/{name}.json"] = dump(t)

    dup_id = copy.deepcopy(b)
    dup_id["id"] = a["id"]
    out["invalid_datasets/duplicate_id/one.json"] = dump(a)
    out["invalid_datasets/duplicate_id/two.json"] = dump(dup_id)

    dup = copy.deepcopy(a)
    dup["id"] = "traj-20261231-903"
    dup["notes"]["final_answer"] += " 重新產生"
    out["invalid_datasets/duplicate_trajectory/traj-20261231-901.json"] = dump(a)
    out["invalid_datasets/duplicate_trajectory/traj-20261231-903.json"] = dump(dup)

    leak = copy.deepcopy(b)
    leak["id"] = "traj-20261231-904"
    leak["group_id"] = a["group_id"]
    leak["notes"]["template_id"] = a["notes"]["template_id"]
    leak["split"] = "test" if a["split"] != "test" else "train"
    out[f"invalid_datasets/split_leakage/{a['id']}.json"] = dump(a)
    out["invalid_datasets/split_leakage/traj-20261231-904.json"] = dump(leak)

    out[f"invalid_datasets/E_MANIFEST/{a['id']}.json"] = dump(a)
    out["invalid_datasets/E_MANIFEST.manifest.json"] = dump(
        {"files": {f"{a['id']}.json": "0" * 64}, "dataset_sha256": "0" * 64}
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    files = build()
    dirs = ("valid", "invalid", "invalid_datasets")
    existing = {
        str(p.relative_to(FIXTURES)): p.read_text("utf-8")
        for d in dirs
        if (FIXTURES / d).is_dir()
        for p in (FIXTURES / d).rglob("*.json")
    }
    if args.check:
        stale = sorted(set(existing) ^ set(files) | {n for n in files if existing.get(n) != files[n]})
        if stale:
            print(f"fixture 不是最新，請執行 python tests/build_fixtures.py：{stale}", file=sys.stderr)
            return 1
        print(f"OK：{len(files)} 個 fixture 檔都是最新")
        return 0
    for name in set(existing) - set(files):
        (FIXTURES / name).unlink()
    for name, body in files.items():
        path = FIXTURES / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, "utf-8")
    print(f"已產生 {len(files)} 個 fixture 檔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
