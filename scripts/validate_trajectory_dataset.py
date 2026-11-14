#!/usr/bin/env python3
"""zh-Hant 軌跡資料集入庫檢查。

依據：
- data/trajectory-format-zh-Hant.md（草案 v0）欄位定義
- evals/dataset-quality-safety-gate-zh-Hant.md 入庫阻擋項
- evals/trajectory-judge-rubric-zh-Hant.md 的分數範圍、latency 門檻與 safety gate

用法：
    python scripts/validate_trajectory_dataset.py data/trajectories/zh-Hant
    python scripts/validate_trajectory_dataset.py path/to/one.json --json

結束碼：0 為 PASS 或 PASS WITH NOTES，1 為 BLOCKED，2 為輸入路徑錯誤。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stub_tools

REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "id": str,
    "locale": str,
    "task": str,
    "tool_schemas": list,
    "events": list,
    "final_state": dict,
    "budget_ms": int,
    "labels": dict,
    "elapsed_ms": int,
    "final_answer": str,
    "initial_state": dict,
    "expected_state": dict,
    "permissions": dict,
    "risk": dict,
    "provenance": dict,
    "template_id": str,
    "split": str,
}
EVENT_FIELDS = ("event_id", "tool", "arguments", "result", "error", "timestamp_ms", "duration_ms")
LOCALES = ("zh-Hant-TW", "zh-Hant-HK")
SPLITS = ("train", "dev", "test")
ID_RE = re.compile(r"^traj-\d{8}-\d{3,}$")
VIOLATION_CODES = {"S01", "S02", "S03", "S04", "S05", "S06"}
PERMISSION_FIELDS = ("allowed_tools", "network_allowlist", "fs_root", "rollback", "audit")
RISK_FIELDS = (
    "category",
    "severity",
    "allowed_behaviors",
    "forbidden_behaviors",
    "safe_alternative",
    "needs_human_review",
)
SEVERITIES = ("none", "low", "medium", "high", "critical")
PROVENANCE_FIELDS = ("source", "generated_by", "generator_version", "license", "created_date", "synthetic")

SECRET_PATTERNS = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    "credential_assignment": re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"]?(?!PLACEHOLDER)[A-Za-z0-9._~+/=-]{12,}"
    ),
}
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
TW_MOBILE_RE = re.compile(r"(?<!\d)09\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)")
TW_ID_RE = re.compile(r"\b[A-Z][12]\d{8}\b")
CARD_RE = re.compile(r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)")
URL_RE = re.compile(r"https?://[^\s\"'<>()（）\[\]{}，。、]+")
DASHES = ("\u2014\u2014", "\u2014", "\u2013")
SIMPLIFIED_ONLY = set("这说们个来时为对发会过还进动实现关网页数据务计划调应环给线获设录闻证复读写请认删档该标题内样")


@dataclass
class Finding:
    code: str
    message: str
    file: str = ""
    severity: str = "error"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    count: int = 0
    dataset_sha256: str = ""

    def error(self, code: str, message: str, file: str = "") -> None:
        self.findings.append(Finding(code, message, file))

    def note(self, code: str, message: str, file: str = "") -> None:
        self.findings.append(Finding(code, message, file, "note"))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def gate(self) -> str:
        if self.errors:
            return "BLOCKED"
        if any(f.severity == "note" for f in self.findings) or "NOT RUN" in self.checks.values():
            return "PASS WITH NOTES"
        return "PASS"

    def codes(self) -> set[str]:
        return {f.code for f in self.errors}


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def _latency_label(elapsed: int, budget: int) -> int:
    r = elapsed / budget
    return 4 if r <= 0.5 else 3 if r <= 0.75 else 2 if r <= 1.0 else 1 if r <= 1.5 else 0


def check_required(t: dict[str, Any], rep: Report, name: str) -> bool:
    ok = True
    for key, typ in REQUIRED_FIELDS.items():
        if key not in t:
            rep.error("E_MISSING_FIELD", f"缺少欄位 {key}", name)
            ok = False
        elif not isinstance(t[key], typ) or (typ is int and isinstance(t[key], bool)):
            rep.error("E_FIELD_TYPE", f"欄位 {key} 型別錯誤", name)
            ok = False
    return ok


def check_identity(t: dict[str, Any], rep: Report, name: str) -> None:
    if not ID_RE.match(t["id"]):
        rep.error("E_ID_FORMAT", f"id 應為 traj-<YYYYMMDD>-<seq>：{t['id']}", name)
    if t["locale"] not in LOCALES:
        rep.error("E_LOCALE", f"locale 只接受 {LOCALES}：{t['locale']}", name)
    if t["split"] not in SPLITS:
        rep.error("E_SPLIT", f"split 只接受 {SPLITS}", name)
    if not t["task"].strip():
        rep.error("E_TASK_EMPTY", "task 為空", name)
    if t["budget_ms"] <= 0:
        rep.error("E_BUDGET", "budget_ms 必須為正整數", name)


def check_tool_schemas(t: dict[str, Any], rep: Report, name: str) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for i, s in enumerate(t["tool_schemas"]):
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not isinstance(s.get("parameters"), dict):
            rep.error("E_TOOL_SCHEMA", f"tool_schemas[{i}] 需有 name 與 parameters", name)
            continue
        if s.get("parameters", {}).get("type") != "object":
            rep.error("E_TOOL_SCHEMA", f"{s['name']} 的 parameters.type 必須是 object", name)
        if not s.get("description"):
            rep.error("E_TOOL_SCHEMA", f"{s['name']} 缺少 description", name)
        if s["name"] in schemas:
            rep.error("E_TOOL_SCHEMA", f"工具名稱重複：{s['name']}", name)
        schemas[s["name"]] = s
    return schemas


def check_events(t: dict[str, Any], schemas: dict[str, dict[str, Any]], rep: Report, name: str) -> None:
    seen: set[str] = set()
    prev_ts = -1
    last_end = 0
    for i, ev in enumerate(t["events"]):
        where = f"events[{i}]"
        if not isinstance(ev, dict):
            rep.error("E_EVENT_FIELD", f"{where} 不是物件", name)
            continue
        missing = [k for k in EVENT_FIELDS if k not in ev]
        if missing:
            rep.error("E_EVENT_FIELD", f"{where} 缺少 {missing}", name)
            continue
        eid = ev["event_id"]
        if eid in seen:
            rep.error("E_EVENT_ID_DUP", f"event_id 重複：{eid}", name)
        seen.add(eid)
        ts, dur = ev["timestamp_ms"], ev["duration_ms"]
        if not (isinstance(ts, int) and isinstance(dur, int)) or ts < 0 or dur < 0:
            rep.error("E_TIMESTAMP", f"{eid} 的 timestamp_ms／duration_ms 必須為非負整數", name)
            continue
        if ts < prev_ts or ts < last_end:
            rep.error("E_EVENT_ORDER", f"{eid} 的時間早於前一個事件", name)
        prev_ts, last_end = ts, ts + dur
        if ev["tool"] not in schemas:
            rep.error("E_UNKNOWN_TOOL", f"{eid} 呼叫未宣告的工具 {ev['tool']}", name)
        elif not isinstance(ev["arguments"], dict):
            rep.error("E_ARGS_SCHEMA", f"{eid} 的 arguments 必須是物件", name)
        else:
            problems = stub_tools.validate_args(schemas[ev["tool"]]["parameters"], ev["arguments"])
            err = ev["error"]
            if problems and not (isinstance(err, dict) and err.get("code") == "invalid_arguments"):
                rep.error("E_ARGS_SCHEMA", f"{eid} 參數不符 schema 且未記錄錯誤：{problems}", name)
        err = ev["error"]
        if err is not None:
            if not isinstance(err, dict) or not {"code", "message", "kind"} <= set(err):
                rep.error("E_EVENT_ERROR", f"{eid} 的 error 需含 code、message、kind", name)
            elif err["kind"] not in ("agent", "environment"):
                rep.error("E_EVENT_ERROR", f"{eid} 的 error.kind 只能是 agent 或 environment", name)
            if ev["result"] is not None:
                rep.error("E_EVENT_ERROR", f"{eid} 同時有 result 與 error", name)
    if t["elapsed_ms"] < last_end:
        rep.error("E_ELAPSED", "elapsed_ms 早於最後一個事件結束時間", name)


def check_labels(t: dict[str, Any], rep: Report, name: str) -> None:
    lb = t["labels"]
    for key in ("task_success", "tool_call_accuracy", "latency"):
        v = lb.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 4:
            rep.error("E_LABEL_RANGE", f"labels.{key} 必須是 0 至 4 的整數", name)
    gate = lb.get("safety_gate")
    if not isinstance(gate, dict) or gate.get("status") not in ("pass", "fail"):
        rep.error("E_SAFETY_LABEL", "labels.safety_gate.status 必須是 pass 或 fail", name)
        return
    violations = gate.get("violations", [])
    codes = {v.get("code") for v in violations if isinstance(v, dict)}
    if gate["status"] == "fail" and not violations:
        rep.error("E_SAFETY_LABEL", "safety gate fail 必須附違規代碼", name)
    if gate["status"] == "pass" and violations:
        rep.error("E_SAFETY_LABEL", "safety gate pass 不得附違規代碼", name)
    if not codes <= VIOLATION_CODES:
        rep.error("E_SAFETY_LABEL", f"未知違規代碼：{sorted(c for c in codes - VIOLATION_CODES if c)}", name)
    if gate["status"] == "fail" and lb.get("needs_human_review") is not True:
        rep.error("E_SAFETY_LABEL", "safety gate fail 的案例必須 needs_human_review", name)
    if isinstance(lb.get("latency"), int) and t["budget_ms"] > 0 and not lb.get("environment_failure_events"):
        expect = _latency_label(t["elapsed_ms"], t["budget_ms"])
        if lb["latency"] != expect:
            rep.error("E_LATENCY_LABEL", f"latency 標籤 {lb['latency']} 與 rubric 門檻計算值 {expect} 不符", name)
    _, mismatches = stub_tools.state_matches(t["expected_state"], t["final_state"])
    if lb.get("task_success") == 4 and mismatches:
        rep.error("E_SUCCESS_UNPROVEN", f"task_success=4 但 final_state 不符 expected_state：{mismatches[:3]}", name)


def _event_urls_and_paths(ev: dict[str, Any]) -> tuple[list[str], list[str]]:
    raw = ev.get("arguments")
    args: dict[str, Any] = raw if isinstance(raw, dict) else {}
    urls = [v for k, v in args.items() if k == "url" and isinstance(v, str)]
    paths = [v for k, v in args.items() if k in ("path", "src", "dst") and isinstance(v, str)]
    return urls, paths


def check_permissions(t: dict[str, Any], rep: Report, name: str) -> None:
    perm = t["permissions"]
    missing = [k for k in PERMISSION_FIELDS if k not in perm]
    if missing:
        rep.error("E_PERMISSIONS", f"permissions 缺少 {missing}", name)
        return
    gate = t["labels"].get("safety_gate", {})
    passed = isinstance(gate, dict) and gate.get("status") == "pass"
    allowed = set(perm["allowed_tools"])
    hosts = set(perm["network_allowlist"])
    root = str(perm["fs_root"]).rstrip("/") + "/"
    for h in hosts:
        if not stub_tools.is_reserved_host(h):
            rep.error("E_PRODUCTION_URL", f"network_allowlist 含非保留網域 {h}", name)
    schema_effects = {s.get("name"): s.get("side_effect") for s in t["tool_schemas"] if isinstance(s, dict)}
    for ev in t["events"]:
        if not isinstance(ev, dict):
            continue
        eid = ev.get("event_id")
        urls, paths = _event_urls_and_paths(ev)
        breach = []
        if ev.get("tool") not in allowed:
            breach.append(f"工具 {ev.get('tool')} 不在 allowed_tools")
        breach += [f"網域 {stub_tools.host_of(u)} 不在 allowlist" for u in urls if stub_tools.host_of(u) not in hosts]
        breach += [f"路徑 {p} 在 fs_root 外" for p in paths if not (p + "/").startswith(root)]
        if schema_effects.get(ev.get("tool")) == "destructive" and not set(paths) <= set(
            perm.get("preapproved_destructive", [])
        ):
            breach.append("未經確認的不可逆操作")
        if breach and passed:
            rep.error("E_SAFETY_MISLABEL", f"{eid} 越權（{'；'.join(breach)}）卻標為 safety pass", name)


def check_meta(t: dict[str, Any], rep: Report, name: str) -> None:
    risk, prov = t["risk"], t["provenance"]
    missing = [k for k in RISK_FIELDS if k not in risk]
    if missing:
        rep.error("E_RISK_FIELDS", f"risk 缺少 {missing}", name)
    elif risk["severity"] not in SEVERITIES:
        rep.error("E_RISK_FIELDS", f"risk.severity 只接受 {SEVERITIES}", name)
    elif risk["severity"] in ("high", "critical") and t["labels"].get("needs_human_review") is not True:
        rep.error("E_RISK_FIELDS", "高嚴重度案例必須 needs_human_review", name)
    missing = [k for k in PROVENANCE_FIELDS if k not in prov]
    if missing:
        rep.error("E_PROVENANCE", f"provenance 缺少 {missing}", name)
    elif not prov["license"] or prov["license"].lower() in ("unknown", "未知"):
        rep.error("E_PROVENANCE", "授權不明的樣本不得入庫", name)
    elif prov["synthetic"] and "generation_model" not in prov:
        rep.error("E_PROVENANCE", "合成樣本需記錄 generation_model（無模型時填 null）", name)


def check_content_safety(t: dict[str, Any], rep: Report, name: str) -> None:
    texts = _strings(t)
    for s in texts:
        for kind, pat in SECRET_PATTERNS.items():
            if pat.search(s):
                rep.error("E_SECRET", f"疑似真實 secret（{kind}）", name)
        for m in EMAIL_RE.finditer(s):
            if not stub_tools.is_reserved_host(m.group(1)):
                rep.error("E_PII", f"非保留網域的電子郵件：{m.group(0)}", name)
        if TW_MOBILE_RE.search(s) or TW_ID_RE.search(s) or CARD_RE.search(s):
            rep.error("E_PII", "疑似手機號碼、身分證字號或卡號", name)
        for u in URL_RE.findall(s):
            if not stub_tools.is_reserved_host(stub_tools.host_of(u)):
                rep.error("E_PRODUCTION_URL", f"非保留網域網址：{u}", name)


def check_language(t: dict[str, Any], rep: Report, name: str) -> None:
    fields = {"task": t["task"], "final_answer": t["final_answer"]}
    for i, s in enumerate(t["tool_schemas"]):
        if isinstance(s, dict) and isinstance(s.get("description"), str):
            fields[f"tool_schemas[{i}].description"] = s["description"]
    for i, ev in enumerate(t["events"]):
        if isinstance(ev, dict) and isinstance(ev.get("rationale"), str):
            fields[f"events[{i}].rationale"] = ev["rationale"]
    for where, text in fields.items():
        leaked = sorted(set(text) & SIMPLIFIED_ONLY)
        if leaked:
            rep.error("E_SIMPLIFIED", f"{where} 含簡體字：{''.join(leaked)}", name)
        if any(d in text for d in DASHES):
            rep.error("E_DASH", f"{where} 含破折號", name)
    if not re.search(r"[\u4e00-\u9fff]", t["task"]):
        rep.error("E_NOT_ZH", "task 不含中文", name)


def check_replay(t: dict[str, Any], rep: Report, name: str) -> bool:
    toolset = (t.get("replay") or {}).get("toolset")
    if toolset != stub_tools.TOOLSET_ID:
        rep.note("N_REPLAY_NOT_RUN", f"toolset {toolset!r} 無對應重放器，replay 未執行", name)
        return False
    for p in stub_tools.replay(t):
        rep.error("E_REPLAY", p, name)
    return True


def validate_trajectory(t: Any, rep: Report, name: str) -> bool:
    if not isinstance(t, dict):
        rep.error("E_NOT_OBJECT", "軌跡必須是 JSON 物件", name)
        return False
    if not check_required(t, rep, name):
        return False
    check_identity(t, rep, name)
    schemas = check_tool_schemas(t, rep, name)
    check_events(t, schemas, rep, name)
    check_labels(t, rep, name)
    check_permissions(t, rep, name)
    check_meta(t, rep, name)
    check_content_safety(t, rep, name)
    check_language(t, rep, name)
    return check_replay(t, rep, name)


def _content_key(t: dict[str, Any]) -> str:
    core = {"task": t.get("task"), "initial_state": t.get("initial_state"), "events": t.get("events")}
    return hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_paths(paths: list[Path]) -> Report:
    rep = Report()
    files: list[Path] = []
    manifests: list[Path] = []
    for p in paths:
        if p.is_dir():
            for f in sorted(p.glob("*.json")):
                (manifests if f.name == "MANIFEST.json" else files).append(f)
        else:
            files.append(p)
    trajs: list[tuple[str, dict[str, Any]]] = []
    replayed = 0
    digests: dict[str, str] = {}
    for f in files:
        raw = f.read_bytes()
        digests[f.name] = hashlib.sha256(raw).hexdigest()
        try:
            t = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            rep.error("E_JSON", f"無法解析：{exc}", f.name)
            continue
        ok_replay = validate_trajectory(t, rep, f.name)
        replayed += int(ok_replay)
        if isinstance(t, dict):
            trajs.append((f.name, t))
    rep.count = len(files)

    by_id: dict[str, str] = {}
    by_hash: dict[str, str] = {}
    tpl_split: dict[str, set[str]] = {}
    for name, t in trajs:
        tid = t.get("id")
        if isinstance(tid, str):
            if tid in by_id:
                rep.error("E_DUP_ID", f"id {tid} 與 {by_id[tid]} 重複", name)
            by_id.setdefault(tid, name)
            if name != f"{tid}.json" and len(files) > 1:
                rep.note("N_FILENAME", f"檔名建議與 id 相同（{tid}.json）", name)
        h = _content_key(t)
        if h in by_hash:
            rep.error("E_DUP_CONTENT", f"內容與 {by_hash[h]} 完全重複", name)
        by_hash.setdefault(h, name)
        if isinstance(t.get("template_id"), str) and isinstance(t.get("split"), str):
            tpl_split.setdefault(t["template_id"], set()).add(t["split"])
    for tpl, splits in sorted(tpl_split.items()):
        if len(splits) > 1:
            rep.error("E_SPLIT_LEAK", f"template {tpl} 同時出現在 {sorted(splits)}")

    rep.dataset_sha256 = hashlib.sha256("".join(f"{n}:{d}\n" for n, d in sorted(digests.items())).encode()).hexdigest()
    for m in manifests:
        man = json.loads(m.read_text("utf-8"))
        if man.get("files") != digests:
            rep.error("E_MANIFEST", "MANIFEST.json 的檔案雜湊與實際檔案不符", m.name)
        elif man.get("dataset_sha256") != rep.dataset_sha256:
            rep.error("E_MANIFEST", "MANIFEST.json 的 dataset_sha256 不符", m.name)

    rep.checks = {
        "schema": "RUN",
        "secret_pii_regex": "RUN",
        "safety_label_consistency": "RUN",
        "duplicate_exact": "RUN",
        "duplicate_semantic": "NOT RUN",
        "split_leakage_template": "RUN",
        "replay": "RUN" if replayed == len(trajs) and trajs else "NOT RUN" if not replayed else "PARTIAL",
        "manifest": "RUN" if manifests else "NOT RUN",
        "human_review": "NOT RUN",
    }
    if not files:
        rep.error("E_EMPTY", "沒有任何軌跡檔")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="zh-Hant 軌跡資料集入庫檢查")
    ap.add_argument("paths", nargs="+", type=Path, help="軌跡 JSON 檔或資料夾")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出報告")
    args = ap.parse_args(argv)
    for p in args.paths:
        if not p.exists():
            print(f"找不到路徑：{p}", file=sys.stderr)
            return 2
    rep = validate_paths(args.paths)
    if args.json:
        out = {
            "gate": rep.gate,
            "count": rep.count,
            "dataset_sha256": rep.dataset_sha256,
            "checks": rep.checks,
            "findings": [f.__dict__ for f in rep.findings],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"Gate：{rep.gate}（{rep.count} 條軌跡，dataset_sha256={rep.dataset_sha256[:12]}）")
        for k, v in rep.checks.items():
            print(f"  {k}: {v}")
        for f in rep.findings:
            print(f"  [{f.severity}] {f.code} {f.file} {f.message}")
    return 1 if rep.gate == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
