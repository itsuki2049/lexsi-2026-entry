"""本專案軌跡與 AgentTune `EventLog` JSON 之間的轉換。

輸出的 dict 結構與 AgentTune `EventLog` 序列化後一致
（`{"id", "tier", "events": [{"kind", "payload"}]}`），
AuditKIT `episode_from_eventlog` 與 `episodes_from_agenttune` 可直接讀取。
時間戳放在 payload 的 `t_start_ms`／`t_end_ms`，兩邊都會忽略未知鍵。
"""

from __future__ import annotations

KIND = {
    "reasoning": "reasoning",
    "message": "text",
    "tool_call": "tool_call",
    "tool_result": "tool_result",
    "final": "text",
}


def to_eventlog(traj: dict) -> dict:
    events = []
    for e in traj.get("events", []):
        t = {"t_start_ms": e["t_start_ms"], "t_end_ms": e["t_end_ms"]}
        kind = KIND[e["type"]]
        if kind == "tool_call":
            payload = {"action": {"name": e["name"], "arguments": e.get("arguments", {})}, "call_id": e.get("call_id")}
        elif kind == "tool_result":
            out = e.get("output") if e.get("error") is None else {"error": e["error"]}
            payload = {"output": out, "call_id": e.get("call_id")}
        else:
            payload = {"text": e.get("text", "")}
        events.append({"kind": kind, "payload": {**payload, **t}})
        if e["type"] == "tool_result":
            events.append({"kind": "turn_complete", "payload": {}})
    return {"id": f"{traj['run_id']}/{traj['task_id']}", "tier": "light", "events": events}


def from_eventlog(log: dict, *, run_id: str, task_id: str, model_id: str) -> dict:
    """AgentTune `EventLog` JSON 轉回本專案格式。沒有時間戳的事件以 0 填入。"""
    events = []
    final = None
    for e in log.get("events", []):
        p = e.get("payload", {})
        t = {"t_start_ms": p.get("t_start_ms", 0), "t_end_ms": p.get("t_end_ms", 0)}
        if e["kind"] == "tool_call":
            action = p.get("action", {})
            events.append(
                {
                    "type": "tool_call",
                    "call_id": p.get("call_id"),
                    "name": action.get("name"),
                    "arguments": action.get("arguments", {}),
                    **t,
                }
            )
        elif e["kind"] == "tool_result":
            events.append(
                {"type": "tool_result", "call_id": p.get("call_id"), "output": p.get("output"), "error": None, **t}
            )
        elif e["kind"] == "reasoning":
            events.append({"type": "reasoning", "text": p.get("text", ""), **t})
        elif e["kind"] == "text":
            final = p.get("text", "")
            events.append({"type": "final", "text": final, **t})
    return {
        "schema_version": "eiraku.traj/0.1",
        "run_id": run_id,
        "task_id": task_id,
        "model": {"id": model_id},
        "status": "completed",
        "events": events,
        "final_answer": final,
    }
