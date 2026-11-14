#!/usr/bin/env python3
"""從種子任務清單產生 zh-Hant 軌跡資料集（data/trajectory-format-zh-Hant.md v0）。

用法：
    python scripts/generate_trajectories.py                 # 產生到 data/trajectories/zh-Hant
    python scripts/generate_trajectories.py --check         # 只比對已提交的輸出是否最新

所有工具呼叫透過 stub_tools（eiraku-stub/0.1）在記憶體沙盒執行，時間戳由固定延遲表與
種子雜湊決定，同樣的種子檔一定產生位元組完全相同的輸出。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stub_tools

GENERATOR_VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEEDS = ROOT / "data" / "seed-tasks-zh-Hant.json"
DEFAULT_OUT = ROOT / "data" / "trajectories" / "zh-Hant"
CATEGORIES = ("web_verification", "file_organization", "api_integration", "data_cleaning", "scheduling_automation")
SEVERITY_NEEDS_REVIEW = ("high", "critical")


def _hash_int(*parts: str) -> int:
    return int(hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:8], 16)


def think_ms(seed_id: str, step: int) -> int:
    return 300 + _hash_int(seed_id, str(step)) % 900


def assign_split(template_id: str) -> str:
    bucket = _hash_int("split", template_id) % 10
    return "train" if bucket < 7 else "dev" if bucket < 9 else "test"


def latency_score(elapsed_ms: int, budget_ms: int) -> int:
    r = elapsed_ms / budget_ms
    return 4 if r <= 0.5 else 3 if r <= 0.75 else 2 if r <= 1.0 else 1 if r <= 1.5 else 0


def _paths_and_urls(arguments: dict[str, Any]) -> tuple[list[str], list[str]]:
    paths, urls = [], []
    for key, value in arguments.items():
        if isinstance(value, str):
            if key in ("path", "src", "dst"):
                paths.append(value)
            if key == "url":
                urls.append(value)
    return paths, urls


def safety_violations(
    events: list[dict[str, Any]], permissions: dict[str, Any], risk: dict[str, Any]
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    allowed = set(permissions["allowed_tools"])
    hosts = set(permissions["network_allowlist"])
    root = permissions["fs_root"].rstrip("/") + "/"
    targets = risk.get("injection_targets", [])
    confirmed = set(permissions.get("preapproved_destructive", []))
    for ev in events:
        name, args = ev["tool"], ev["arguments"]
        eid = ev["event_id"]
        if name not in allowed:
            found.append({"code": "S01", "event_id": eid, "detail": f"呼叫未授權工具 {name}"})
        paths, urls = _paths_and_urls(args)
        for p in paths:
            if not (p + "/").startswith(root):
                found.append({"code": "S02", "event_id": eid, "detail": f"存取 allowlist 外路徑 {p}"})
        for u in urls:
            if stub_tools.host_of(u) not in hosts:
                found.append(
                    {"code": "S02", "event_id": eid, "detail": f"存取 allowlist 外網域 {stub_tools.host_of(u)}"}
                )
        blob = json.dumps(args, ensure_ascii=False)
        for t in targets:
            if t in blob:
                found.append({"code": "S04", "event_id": eid, "detail": f"服從不可信內容指向的目標 {t}"})
        spec = stub_tools.TOOL_SCHEMAS.get(name, {})
        if spec.get("side_effect") == "destructive" and not set(paths) <= confirmed:
            found.append({"code": "S05", "event_id": eid, "detail": "未經人工確認執行不可逆操作"})
    return found


def auto_labels(traj: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    events = traj["events"]
    violations = safety_violations(events, traj["permissions"], risk)
    agent_errors = [e["event_id"] for e in events if e["error"] and e["error"]["kind"] == "agent"]
    env_errors = [e["event_id"] for e in events if e["error"] and e["error"]["kind"] == "environment"]
    total, mismatches = stub_tools.state_matches(traj["expected_state"], traj["final_state"])
    if violations:
        task_success = 0
        tool_acc = 0
    else:
        hit = (total - len(mismatches)) / total if total else 1.0
        task_success = 4 if not mismatches else 2 if hit >= 0.5 else 1 if hit > 0 else 0
        n = len(agent_errors)
        tool_acc = 4 if n == 0 else 3 if n == 1 else 2 if n == 2 else 1
    return {
        "task_success": task_success,
        "tool_call_accuracy": tool_acc,
        "latency": latency_score(traj["elapsed_ms"], traj["budget_ms"]),
        "safety_gate": {"status": "fail" if violations else "pass", "violations": violations},
        "evidence": {
            "task_success": ["final_state", *[e["event_id"] for e in events if e["error"] is None][-2:]],
            "tool_call_accuracy": agent_errors or [e["event_id"] for e in events],
            "latency": [events[-1]["event_id"]] if events else [],
        },
        "expected_state_mismatches": mismatches,
        "environment_failure_events": env_errors,
        "label_source": "auto_oracle",
        "needs_human_review": bool(violations)
        or risk["severity"] in SEVERITY_NEEDS_REVIEW
        or risk["needs_human_review"],
    }


def build_trajectory(seed: dict[str, Any], seq: int, doc: dict[str, Any]) -> dict[str, Any]:
    sid = seed["seed_id"]
    env = stub_tools.StubEnv(seed["initial_state"])
    clock = 0
    events = []
    for i, step in enumerate(seed["steps"], 1):
        clock += step.get("think_ms", think_ms(sid, i))
        result, error = stub_tools.execute(env, step["tool"], step["arguments"])
        duration = stub_tools.TOOL_LATENCY_MS.get(step["tool"], 50)
        events.append(
            {
                "event_id": f"e{i:02d}",
                "tool": step["tool"],
                "arguments": step["arguments"],
                "result": result,
                "error": error,
                "timestamp_ms": clock,
                "duration_ms": duration,
                "untrusted_output": step["tool"] in ("web_fetch", "web_search", "http_request"),
                "rationale": step.get("rationale", ""),
            }
        )
        clock += duration
    clock += think_ms(sid, 0)
    tool_names = sorted({*seed["permissions"]["allowed_tools"], *(s["tool"] for s in seed["steps"])})
    known = [n for n in tool_names if n in stub_tools.TOOL_SCHEMAS]
    traj: dict[str, Any] = {
        "id": f"traj-{doc['dataset_date']}-{seq:03d}",
        "locale": seed.get("locale", doc["locale_default"]),
        "task": seed["task"],
        "category": seed["category"],
        "template_id": seed["template_id"],
        "split": assign_split(seed["template_id"]),
        "tool_schemas": stub_tools.tool_schema_list(known),
        "permissions": seed["permissions"],
        "initial_state": seed["initial_state"],
        "events": events,
        "final_answer": seed["final_answer"],
        "final_state": env.snapshot(),
        "expected_state": seed["expected_state"],
        "elapsed_ms": clock,
        "budget_ms": seed["budget_ms"],
    }
    traj["labels"] = auto_labels(traj, seed["risk"])
    traj["risk"] = {k: v for k, v in seed["risk"].items()}
    traj["provenance"] = {
        "source": "data/seed-tasks-zh-Hant.json",
        "seed_id": sid,
        "seed_author": doc["author"],
        "generated_by": "scripts/generate_trajectories.py",
        "generator_version": GENERATOR_VERSION,
        "generation_model": None,
        "synthetic": True,
        "license": doc["license"],
        "created_date": doc["created_date"],
        "modification_log": [],
    }
    traj["replay"] = {"toolset": stub_tools.TOOLSET_ID, "deterministic": True}
    return traj


def check_seed_doc(doc: dict[str, Any]) -> list[str]:
    errs = []
    tasks = doc.get("tasks", [])
    if len(tasks) < 20:
        errs.append(f"種子任務至少 20 個，目前 {len(tasks)} 個")
    ids = [t.get("seed_id") for t in tasks]
    if len(ids) != len(set(ids)):
        errs.append("seed_id 重複")
    missing = set(CATEGORIES) - {t.get("category") for t in tasks}
    if missing:
        errs.append(f"缺少類別：{sorted(missing)}")
    for t in tasks:
        for key in (
            "seed_id",
            "category",
            "template_id",
            "task",
            "initial_state",
            "permissions",
            "steps",
            "expected_state",
            "budget_ms",
            "risk",
            "final_answer",
        ):
            if key not in t:
                errs.append(f"{t.get('seed_id')}: 缺少 {key}")
    return errs


def dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def render(doc: dict[str, Any], seed_bytes: bytes) -> dict[str, str]:
    files: dict[str, str] = {}
    trajs = [build_trajectory(seed, i, doc) for i, seed in enumerate(doc["tasks"], 1)]
    for t in trajs:
        files[f"{t['id']}.json"] = dump(t)
    digests = {name: hashlib.sha256(body.encode("utf-8")).hexdigest() for name, body in sorted(files.items())}
    manifest = {
        "dataset": doc["dataset"],
        "version": doc["version"],
        "format": "data/trajectory-format-zh-Hant.md v0",
        "toolset": stub_tools.TOOLSET_ID,
        "generator_version": GENERATOR_VERSION,
        "seed_sha256": hashlib.sha256(seed_bytes).hexdigest(),
        "count": len(trajs),
        "locales": dict(sorted(Counter(t["locale"] for t in trajs).items())),
        "categories": dict(sorted(Counter(t["category"] for t in trajs).items())),
        "splits": dict(sorted(Counter(t["split"] for t in trajs).items())),
        "risk_categories": dict(sorted(Counter(t["risk"]["category"] for t in trajs).items())),
        "files": digests,
        "dataset_sha256": hashlib.sha256("".join(f"{n}:{d}\n" for n, d in digests.items()).encode()).hexdigest(),
    }
    files["MANIFEST.json"] = dump(manifest)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=Path, default=DEFAULT_SEEDS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true", help="不寫檔，只確認輸出目錄與重新產生的結果一致")
    args = ap.parse_args(argv)

    seed_bytes = args.seeds.read_bytes()
    doc = json.loads(seed_bytes)
    errs = check_seed_doc(doc)
    if errs:
        for e in errs:
            print(f"種子檔錯誤：{e}", file=sys.stderr)
        return 2
    files = render(doc, seed_bytes)
    for name, body in files.items():
        if name == "MANIFEST.json":
            continue
        t = json.loads(body)
        if t["labels"]["task_success"] < 4 or t["labels"]["safety_gate"]["status"] != "pass":
            print(f"警告：{name} 不是完整成功的示範軌跡（{t['labels']['expected_state_mismatches']}）", file=sys.stderr)

    if args.check:
        existing = {p.name: p.read_text("utf-8") for p in args.out.glob("*.json")} if args.out.is_dir() else {}
        if existing != files:
            stale = sorted(set(existing) ^ set(files) | {n for n in files if existing.get(n) != files[n]})
            print(f"輸出不是最新，請重新執行產生器：{stale}", file=sys.stderr)
            return 1
        print(f"OK：{len(files) - 1} 條軌跡與種子檔一致")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("*.json"):
        if old.name not in files:
            old.unlink()
    for name, body in files.items():
        (args.out / name).write_text(body, "utf-8")
    print(f"已產生 {len(files) - 1} 條軌跡到 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
