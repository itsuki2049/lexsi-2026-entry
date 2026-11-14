"""用 AuditKIT 交叉驗證本 harness 的工具呼叫分數。

python -m evals.adapters.auditkit_xcheck --tasks ... --tools ... --runs ...

需要 `pip install auditkit==1.1.11`（零第三方相依）。檢查兩件事：
1. 本 harness `exact` 模式的 F1 與 exact_set，逐題等於 AuditKIT `ToolCallF1()` 的 tool_call_f1 與 tool_call_exact。
2. 軌跡轉成 AgentTune EventLog 後，AuditKIT `episode_from_eventlog` 讀出的工具呼叫與原軌跡相同。
任何一題不一致就以非零結束碼退出。
"""

from __future__ import annotations

import argparse
import sys

from ..metrics import tool_call_accuracy, tool_calls
from ..schema import load_tools, read_jsonl
from .agenttune import to_eventlog


def xcheck(tasks: list[dict], tools: dict[str, dict], runs: list[dict]) -> list[str]:
    import auditkit as ak
    from auditkit.agent_eval.importers import episode_from_eventlog

    by_id = {t["task_id"]: t for t in tasks}
    samples, ours = [], []
    for traj in runs:
        task = by_id[traj["task_id"]]
        pred = tool_calls(traj)
        ref = task["reference"].get("tool_calls", [])
        samples.append(
            ak.Sample(
                input=task["instruction"],
                actual_output=traj.get("final_answer") or "",
                actual_trace={"tool_calls": [[c] for c in pred]},
                expected_tool_calls=[[c] for c in ref],
            )
        )
        ours.append((traj, pred, tool_call_accuracy(pred, ref, "exact")))

    result = ak.evaluate(samples, "precomputed", [ak.ToolCallF1()])
    problems = []
    if len(result.predictions) != len(ours):
        return [f"auditkit returned {len(result.predictions)} predictions for {len(ours)} episodes"]
    pairs = (("tool_call_f1", "f1"), ("tool_call_exact", "exact_set"))
    for pred_row, (traj, pred, acc) in zip(result.predictions, ours, strict=True):
        theirs = {s["name"]: s["value"] for s in pred_row.metadata["scores"]}
        for ak_name, our_name in pairs:
            if abs(theirs[ak_name] - acc[our_name]) > 1e-9:
                problems.append(
                    f"{traj['run_id']}/{traj['task_id']}: {our_name} ours={acc[our_name]} "
                    f"auditkit {ak_name}={theirs[ak_name]}"
                )
        episode = episode_from_eventlog(to_eventlog(traj))
        back = [c for turn in episode.to_trace()["tool_calls"] for c in turn]
        if back != pred:
            problems.append(f"{traj['run_id']}/{traj['task_id']}: eventlog round trip changed tool calls")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--tools", required=True)
    ap.add_argument("--runs", required=True, nargs="+")
    args = ap.parse_args(argv)
    runs = [r for p in args.runs for r in read_jsonl(p)]
    problems = xcheck(read_jsonl(args.tasks), load_tools(args.tools), runs)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"auditkit xcheck ok: {len(runs)} episodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
