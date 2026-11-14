"""載入與驗證 data/ 下的任務、工具與軌跡檔（規格見 data/README.md）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import SPEC_VERSION

LANGS = {"zh-Hant", "en"}
STATUSES = {"completed", "error", "timeout"}
EVENT_TYPES = {"reasoning", "message", "tool_call", "tool_result", "final"}


def read_jsonl(path: str | Path) -> list[dict]:
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{n}: {e}") from e
    return rows


def load_tools(path: str | Path) -> dict[str, dict]:
    tools = json.loads(Path(path).read_text(encoding="utf-8"))
    return {t["function"]["name"]: t["function"] for t in tools}


def sha256_of(paths: list[str | Path]) -> dict[str, str]:
    return {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


def validate_task(task: dict, tools: dict[str, dict]) -> list[str]:
    errs = []
    for k in ("task_id", "pair_id", "lang", "instruction", "tools", "reference"):
        if k not in task:
            errs.append(f"missing {k!r}")
    if task.get("lang") not in LANGS:
        errs.append(f"lang must be one of {sorted(LANGS)}")
    for name in task.get("tools", []):
        if name not in tools:
            errs.append(f"unknown tool {name!r}")
    for call in task.get("reference", {}).get("tool_calls", []):
        if call.get("name") not in task.get("tools", []):
            errs.append(f"reference calls tool not offered: {call.get('name')!r}")
    return errs


def validate_trajectory(traj: dict, task_ids: set[str]) -> list[str]:
    errs = []
    if traj.get("schema_version") != SPEC_VERSION:
        errs.append(f"schema_version must be {SPEC_VERSION!r}")
    for k in ("run_id", "task_id", "model", "status", "events"):
        if k not in traj:
            errs.append(f"missing {k!r}")
    if traj.get("task_id") not in task_ids:
        errs.append(f"unknown task_id {traj.get('task_id')!r}")
    if traj.get("status") not in STATUSES:
        errs.append(f"status must be one of {sorted(STATUSES)}")
    last_start: float = 0
    open_calls: set[str] = set()
    for i, e in enumerate(traj.get("events", [])):
        where = f"events[{i}]"
        if e.get("type") not in EVENT_TYPES:
            errs.append(f"{where}: bad type {e.get('type')!r}")
        s, t = e.get("t_start_ms"), e.get("t_end_ms")
        if not isinstance(s, (int, float)) or not isinstance(t, (int, float)) or s < 0 or t < s:
            errs.append(f"{where}: need 0 <= t_start_ms <= t_end_ms")
        elif s < last_start:
            errs.append(f"{where}: events must be ordered by t_start_ms")
        else:
            last_start = s
        if e.get("type") == "tool_call":
            if not e.get("name") or not isinstance(e.get("arguments"), dict):
                errs.append(f"{where}: tool_call needs name and object arguments")
            open_calls.add(e.get("call_id"))
        if e.get("type") == "tool_result" and e.get("call_id") not in open_calls:
            errs.append(f"{where}: tool_result call_id has no earlier tool_call")
    return errs


def validate_all(tasks: list[dict], tools: dict[str, dict], runs: list[dict]) -> list[str]:
    out = []
    seen: set[str] = set()
    for t in tasks:
        if t.get("task_id") in seen:
            out.append(f"task {t.get('task_id')}: duplicate task_id")
        seen.add(str(t.get("task_id")))
        out += [f"task {t.get('task_id')}: {e}" for e in validate_task(t, tools)]
    for r in runs:
        out += [f"run {r.get('run_id')}/{r.get('task_id')}: {e}" for e in validate_trajectory(r, seen)]
    return out
