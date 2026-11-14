"""評測入口：python -m evals.run --tasks ... --tools ... --runs ... --out ...

輸出為決定性 JSON（排序鍵、不含執行時間戳），同樣輸入必得同樣位元組。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from . import SPEC_VERSION
from .metrics import (
    ARG_MODES,
    episode_latency,
    latency_summary,
    schema_validity,
    simplified_leak,
    task_outcome,
    tool_call_accuracy,
    tool_calls,
)
from .schema import load_tools, read_jsonl, sha256_of, validate_all


def score_episode(task: dict, traj: dict | None, tools: dict[str, dict]) -> dict:
    pred = tool_calls(traj) if traj else []
    ref = task["reference"].get("tool_calls", [])
    lat = episode_latency(traj) if traj else episode_latency({})
    budget = task.get("budgets", {}).get("max_latency_ms")
    outcome = task_outcome(traj, task)
    return {
        "task_id": task["task_id"],
        "pair_id": task["pair_id"],
        "lang": task["lang"],
        "run_id": traj.get("run_id") if traj else None,
        "model": (traj.get("model") or {}).get("id") if traj else None,
        "outcome": outcome,
        "tool_call": {m: tool_call_accuracy(pred, ref, m) for m in ARG_MODES},
        "schema_validity": schema_validity(pred, tools),
        "n_tool_calls": len(pred),
        "latency": lat,
        "within_budget": None if budget is None or lat["total_ms"] is None else lat["total_ms"] <= budget,
        "simplified_leak": simplified_leak(traj, task),
    }


def _mean(values: list[float | None]) -> float | None:
    xs = [x for x in values if x is not None]
    return sum(xs) / len(xs) if xs else None


def aggregate(rows: list[dict]) -> dict:
    verdicts = [r["outcome"]["verdict"] for r in rows]
    scored = [v for v in verdicts if v in ("success", "failure")]
    succ = verdicts.count("success")
    leaks = [r["simplified_leak"] for r in rows if r["simplified_leak"] is not None]
    return {
        "n": len(rows),
        "task_success": {
            "rate_all": succ / len(rows) if rows else None,
            "rate_scored": succ / len(scored) if scored else None,
            "success_within_budget": _mean(
                [float(r["outcome"]["verdict"] == "success" and bool(r["within_budget"])) for r in rows]
            ),
            "verdicts": {v: verdicts.count(v) for v in sorted(set(verdicts))},
        },
        "tool_call": {
            m: {k: _mean([r["tool_call"][m][k] for r in rows]) for k in ("f1", "exact_set", "exact_sequence")}
            for m in ARG_MODES
        },
        "schema_validity": _mean([r["schema_validity"] for r in rows]),
        "latency_ms": {
            k: latency_summary(r["latency"][k] for r in rows)
            for k in ("total_ms", "model_ms", "tool_ms", "first_tool_call_ms")
        },
        "simplified_leak_rate": _mean([float(bool(x)) for x in leaks]) if leaks else None,
    }


def evaluate(tasks: list[dict], tools: dict[str, dict], runs: list[dict]) -> dict:
    by_key = {(r["run_id"], r["task_id"]): r for r in runs}
    run_ids = sorted({r["run_id"] for r in runs})
    report: dict = {"spec_version": SPEC_VERSION, "runs": {}}
    for run_id in run_ids:
        rows = [score_episode(t, by_key.get((run_id, t["task_id"])), tools) for t in tasks]
        by_lang = defaultdict(list)
        for row in rows:
            by_lang[row["lang"]].append(row)
        report["runs"][run_id] = {
            "overall": aggregate(rows),
            "by_lang": {lang: aggregate(rs) for lang, rs in sorted(by_lang.items())},
            "episodes": rows,
        }
    return report


def _fmt(x) -> str:
    if x is None:
        return "n/a"
    return f"{x:.3f}" if isinstance(x, float) else str(x)


def to_markdown(report: dict) -> str:
    lines = [
        "| run | lang | n | 任務成功率 | 工具呼叫 F1（exact） | 工具呼叫 F1（zh_norm） | Schema 合法率 | 延遲 p50 ms | 延遲 p95 ms | 簡體洩漏率 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run_id, r in report["runs"].items():
        for lang, a in r["by_lang"].items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        run_id,
                        lang,
                        str(a["n"]),
                        _fmt(a["task_success"]["rate_all"]),
                        _fmt(a["tool_call"]["exact"]["f1"]),
                        _fmt(a["tool_call"]["zh_norm"]["f1"]),
                        _fmt(a["schema_validity"]),
                        _fmt(a["latency_ms"]["total_ms"]["p50"]),
                        _fmt(a["latency_ms"]["total_ms"]["p95"]),
                        _fmt(a["simplified_leak_rate"]),
                    ]
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--tools", required=True)
    ap.add_argument("--runs", required=True, nargs="+")
    ap.add_argument("--out", required=True, help="JSON report path")
    ap.add_argument("--markdown", help="optional Markdown summary path")
    args = ap.parse_args(argv)

    tasks = read_jsonl(args.tasks)
    tools = load_tools(args.tools)
    runs = [r for p in args.runs for r in read_jsonl(p)]
    errors = validate_all(tasks, tools, runs)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    report = evaluate(tasks, tools, runs)
    report["inputs_sha256"] = sha256_of([args.tasks, args.tools, *args.runs])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = to_markdown(report)
    if args.markdown:
        Path(args.markdown).write_text(md, encoding="utf-8")
    print(md, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
