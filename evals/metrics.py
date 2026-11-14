"""T3 三指標：任務成功率、工具呼叫正確率、延遲。

每個函式只吃 dict（data/README.md 定義的格式），不依賴任何第三方套件。
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable

from .normalize import normalize_text, normalize_value, simplified_chars

ARG_MODES = ("exact", "zh_norm", "name")
MODEL_EVENTS = {"reasoning", "message", "tool_call", "final"}


# 工具呼叫 ------------------------------------------------------------------


def tool_calls(traj: dict) -> list[dict]:
    return [
        {"name": e["name"], "arguments": e.get("arguments", {})}
        for e in traj.get("events", [])
        if e.get("type") == "tool_call"
    ]


def _call_key(call: dict, mode: str) -> str:
    if mode == "name":
        return call["name"]
    args = call.get("arguments", {})
    if mode == "zh_norm":
        args = normalize_value(args)
    return call["name"] + "\x00" + json.dumps(args, sort_keys=True, ensure_ascii=False)


def tool_call_accuracy(pred: list[dict], ref: list[dict], mode: str = "exact") -> dict:
    """多重集合 precision / recall / F1，加上「集合完全相同」與「順序完全相同」。

    與 AuditKIT `ToolCallF1` 的 exact 模式語意一致（重複呼叫會計次、忽略回合邊界），
    差別是多了 `zh_norm` 模式。參考答案為空時，模型不呼叫工具才算對。
    """
    if mode not in ARG_MODES:
        raise ValueError(f"mode must be one of {ARG_MODES}, got {mode!r}")
    pred_keys = [_call_key(c, mode) for c in pred]
    ref_keys = [_call_key(c, mode) for c in ref]
    remaining = list(ref_keys)
    matched = 0
    for k in pred_keys:
        if k in remaining:
            remaining.remove(k)
            matched += 1
    if not ref_keys:
        p = r = f = 1.0 if not pred_keys else 0.0
    else:
        p = matched / len(pred_keys) if pred_keys else 0.0
        r = matched / len(ref_keys)
        f = 2 * p * r / (p + r) if p + r else 0.0
    return {
        "precision": p,
        "recall": r,
        "f1": f,
        "exact_set": float(matched == len(ref_keys) == len(pred_keys)),
        "exact_sequence": float(pred_keys == ref_keys),
    }


_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_call(call: dict, tools: dict[str, dict]) -> list[str]:
    """依工具 JSON Schema（OpenAI function 格式的 parameters）檢查一次呼叫。"""
    spec = tools.get(call["name"])
    if spec is None:
        return [f"unknown tool {call['name']!r}"]
    params = spec.get("parameters", {})
    props = params.get("properties", {})
    args = call.get("arguments", {})
    if not isinstance(args, dict):
        return ["arguments is not an object"]
    errors = [f"missing required {k!r}" for k in params.get("required", []) if k not in args]
    for k, v in args.items():
        if k not in props:
            errors.append(f"undeclared argument {k!r}")
            continue
        p = props[k]
        t = _JSON_TYPES.get(p.get("type", ""))
        is_bool = isinstance(v, bool)
        if t is not None and (not isinstance(v, t) or (is_bool and p.get("type") != "boolean")):
            errors.append(f"{k!r} expected {p['type']}")
        if "enum" in p and v not in p["enum"]:
            errors.append(f"{k!r} not in enum")
        if "pattern" in p and isinstance(v, str):
            import re

            if not re.fullmatch(p["pattern"], v):
                errors.append(f"{k!r} does not match pattern")
    return errors


def schema_validity(pred: list[dict], tools: dict[str, dict]) -> float | None:
    if not pred:
        return None
    return sum(not validate_call(c, tools) for c in pred) / len(pred)


# 任務成功 ------------------------------------------------------------------


def task_outcome(traj: dict | None, task: dict) -> dict:
    """回傳 verdict：success、failure、unknown（沒有 oracle）、error（執行失敗或缺軌跡）。

    oracle 有兩種，可同時使用（皆須成立）：
    `reference.answer.contains`：最終答案正規化後須包含每個字串。
    `reference.final_state`：最終狀態的每個鍵值須相等（正規化後比較）。
    """
    if traj is None:
        return {"verdict": "error", "reason": "missing trajectory"}
    if traj.get("status") != "completed":
        return {"verdict": "error", "reason": f"status={traj.get('status')}"}
    ref = task.get("reference", {})
    checks: list[tuple[str, bool]] = []
    answer = ref.get("answer")
    if answer:
        got = normalize_text(traj.get("final_answer") or "")
        for needle in answer.get("contains", []):
            checks.append((f"answer contains {needle!r}", normalize_text(needle) in got))
        if "equals" in answer:
            checks.append(("answer equals", normalize_text(answer["equals"]) == got))
    state = ref.get("final_state")
    if state:
        actual = traj.get("final_state") or {}
        for k, v in state.items():
            checks.append((f"state {k!r}", normalize_value(actual.get(k)) == normalize_value(v)))
    if not checks:
        return {"verdict": "unknown", "reason": "no oracle"}
    failed = [name for name, ok in checks if not ok]
    if failed:
        return {"verdict": "failure", "reason": "; ".join(failed)}
    return {"verdict": "success", "reason": ""}


# 延遲 ----------------------------------------------------------------------


def episode_latency(traj: dict) -> dict:
    """事件時間戳以 episode 起點為 0 的毫秒數表示。"""
    events = traj.get("events", [])
    if not events:
        return {"total_ms": None, "model_ms": None, "tool_ms": None, "first_tool_call_ms": None}
    total = max(e["t_end_ms"] for e in events) - min(e["t_start_ms"] for e in events)
    model = sum(e["t_end_ms"] - e["t_start_ms"] for e in events if e["type"] in MODEL_EVENTS)
    tool = sum(e["t_end_ms"] - e["t_start_ms"] for e in events if e["type"] == "tool_result")
    first = next((e["t_end_ms"] for e in events if e["type"] == "tool_call"), None)
    return {"total_ms": total, "model_ms": model, "tool_ms": tool, "first_tool_call_ms": first}


def percentile(values: Iterable[float], q: float) -> float | None:
    """最近秩（nearest rank）百分位數，結果必為樣本中的某個值，方便重現。"""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    rank = max(1, math.ceil(q / 100 * len(xs)))
    return float(xs[rank - 1])


def latency_summary(values: Iterable[float | None]) -> dict:
    xs = [v for v in values if v is not None]
    return {
        "n": len(xs),
        "mean": sum(xs) / len(xs) if xs else None,
        "p50": percentile(xs, 50),
        "p95": percentile(xs, 95),
        "max": float(max(xs)) if xs else None,
    }


# 繁中輔助指標 --------------------------------------------------------------


def simplified_leak(traj: dict | None, task: dict) -> list[str] | None:
    """zh-Hant 任務的最終答案若含簡體專用字，回傳那些字；非 zh-Hant 任務回傳 None。"""
    if task.get("lang") != "zh-Hant" or traj is None:
        return None
    return simplified_chars(traj.get("final_answer") or "")
