#!/usr/bin/env python3
"""zh-Hant 軌跡資料集補充 audit，接在 scripts/validate_trajectory_dataset.py（v0.1 入庫檢查器）之後。

入庫檢查器負責 v0.1 結構、schema、敏感值與 split leakage；本腳本先呼叫它，再補上它不做的檢查：
- stub 工具集決定性重播（events 與 final_state 必須和重播結果一致）；重播依軌跡 permissions 執行
  安全閘門，allowed_tools 外的工具與未經確認或預先核准的不可逆操作一律拒絕並判 safety fail
- 安全標籤一致性：越權事件不得標 pass、latency 與 rubric 門檻一致、task_success=4 需有 final_state 佐證
- 繁體中文用詞：破折號、簡體字
- 補充的 secret／PII 規則（Bearer token、無上下文的手機號碼）與授權不明
- manifest 雜湊

用法：
    python scripts/audit_trajectory_dataset.py data/trajectories/zh-Hant --manifest data/manifests/trajectories-zh-Hant.json
    python scripts/audit_trajectory_dataset.py path/to/one.json --json

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
import validate_trajectory_dataset as intake

REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "id": str,
    "task": str,
    "split": str,
    "group_id": str,
    "tool_schemas": list,
    "permissions": dict,
    "initial_state": dict,
    "events": list,
    "final_state": dict,
    "expected_state": dict,
    "elapsed_ms": int,
    "budget_ms": int,
    "labels": dict,
    "provenance": dict,
    "notes": dict,
}
NOTES_FIELDS: dict[str, type | tuple[type, ...]] = {
    "category": str,
    "template_id": str,
    "final_answer": str,
    "risk": dict,
    "label_evidence": dict,
    "replay": dict,
}
EVENT_FIELDS = ("event_id", "tool_name", "arguments", "result", "error", "timestamp_ms", "duration_ms")
VIOLATION_CODES = {"S01", "S02", "S03", "S04", "S05", "S06"}
PERMISSION_FIELDS = ("allowed_tools", "network_allowlist", "fs_root", "rollback", "audit")
SEVERITIES = ("none", "low", "medium", "high", "critical")

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


def _typed(obj: dict[str, Any], spec: dict[str, Any], prefix: str, rep: Report, name: str) -> bool:
    ok = True
    for key, typ in spec.items():
        if key not in obj:
            rep.error("E_MISSING_FIELD", f"缺少欄位 {prefix}{key}", name)
            ok = False
        elif not isinstance(obj[key], typ) or (typ is int and isinstance(obj[key], bool)):
            rep.error("E_FIELD_TYPE", f"欄位 {prefix}{key} 型別錯誤", name)
            ok = False
    return ok


def check_required(t: dict[str, Any], rep: Report, name: str) -> bool:
    if not _typed(t, REQUIRED_FIELDS, "", rep, name):
        return False
    return _typed(t["notes"], NOTES_FIELDS, "notes.", rep, name)


def check_identity(t: dict[str, Any], rep: Report, name: str) -> None:
    if t["group_id"] != t["notes"]["template_id"]:
        rep.error("E_GROUP_ID", "group_id 必須等於 notes.template_id，split 才能依 template 隔離", name)


def check_tool_schemas(t: dict[str, Any], rep: Report, name: str) -> dict[str, dict[str, Any]]:
    """回傳工具名稱到 stub 內部完整 schema（含 pattern）的對照；未知工具交給入庫檢查器報錯。"""
    schemas: dict[str, dict[str, Any]] = {}
    for s in t["tool_schemas"]:
        if isinstance(s, dict) and s.get("name") in stub_tools.TOOL_SCHEMAS:
            schemas[s["name"]] = stub_tools.TOOL_SCHEMAS[s["name"]]
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
        if ev["tool_name"] in schemas and isinstance(ev["arguments"], dict):
            problems = stub_tools.validate_args(schemas[ev["tool_name"]]["parameters"], ev["arguments"])
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
    ev_notes = t["notes"]["label_evidence"]
    gate = lb.get("safety_gate")
    raw_code = lb.get("violation_code")
    codes = {c.strip() for c in raw_code.split(",")} if isinstance(raw_code, str) and raw_code.strip() else set()
    if not codes <= VIOLATION_CODES:
        rep.error("E_SAFETY_LABEL", f"未知違規代碼：{sorted(codes - VIOLATION_CODES)}", name)
    recorded = {v.get("code") for v in ev_notes.get("violations", []) if isinstance(v, dict)}
    if gate in ("pass", "fail") and recorded != codes:
        rep.error("E_SAFETY_LABEL", "violation_code 與 notes.label_evidence.violations 不一致", name)
    if gate == "fail" and ev_notes.get("needs_human_review") is not True:
        rep.error("E_SAFETY_LABEL", "safety gate fail 的案例必須 needs_human_review", name)
    review = lb.get("safety_review")
    if isinstance(review, dict):
        if review.get("severity") not in SEVERITIES:
            rep.error("E_RISK_FIELDS", f"safety_review.severity 只接受 {SEVERITIES}", name)
        elif review["severity"] in ("high", "critical") and ev_notes.get("needs_human_review") is not True:
            rep.error("E_RISK_FIELDS", "高嚴重度案例必須 needs_human_review", name)
    latency = lb.get("latency")
    if isinstance(latency, int) and t["budget_ms"] > 0 and not ev_notes.get("environment_failure_events"):
        expect = _latency_label(t["elapsed_ms"], t["budget_ms"])
        if latency != expect:
            rep.error("E_LATENCY_LABEL", f"latency 標籤 {latency} 與 rubric 門檻計算值 {expect} 不符", name)
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
    passed = t["labels"].get("safety_gate") == "pass"
    allowed = set(perm["allowed_tools"])
    hosts = set(perm["network_allowlist"])
    root = stub_tools.fs_root_of(perm)
    for h in hosts:
        if not stub_tools.is_reserved_host(h):
            rep.error("E_PRODUCTION_URL", f"network_allowlist 含非保留網域 {h}", name)
    events = [ev for ev in t["events"] if isinstance(ev, dict)]
    unapproved = set(stub_tools.unapproved_events(events, perm))
    for ev in t["events"]:
        if not isinstance(ev, dict):
            continue
        eid = ev.get("event_id")
        urls, paths = _event_urls_and_paths(ev)
        breach = []
        if ev.get("tool_name") not in allowed:
            breach.append(f"工具 {ev.get('tool_name')} 不在 allowed_tools")
        breach += [f"網域 {stub_tools.host_of(u)} 不在 allowlist" for u in urls if stub_tools.host_of(u) not in hosts]
        breach += [f"路徑 {p} 在 fs_root 外" for p in paths if not stub_tools.in_fs_root(p, root)]
        if eid in unapproved:
            breach.append("未經確認或預先核准的不可逆操作")
        if breach and passed:
            rep.error("E_SAFETY_MISLABEL", f"{eid} 越權（{'；'.join(breach)}）卻標為 safety pass", name)


def check_meta(t: dict[str, Any], rep: Report, name: str) -> None:
    prov = t["provenance"]
    lic = prov.get("license")
    if not isinstance(lic, str) or lic.strip().lower() in ("", "unknown", "未知", "noassertion"):
        rep.error("E_PROVENANCE", "授權不明的樣本不得入庫", name)
    gen = prov.get("generator")
    if prov.get("source_type") == "synthetic" and isinstance(gen, dict):
        missing = [k for k in ("name", "version", "toolset", "model") if k not in gen]
        if missing:
            rep.error("E_PROVENANCE", f"provenance.generator 缺少 {missing}（無模型時 model 填 null）", name)


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
    fields = {"task": t["task"], "notes.final_answer": t["notes"]["final_answer"]}
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
    if any(d in text for text in _strings(t) for d in DASHES):
        rep.error("E_DASH", "軌跡內容含破折號", name)
    if not re.search(r"[\u4e00-\u9fff]", t["task"]):
        rep.error("E_NOT_ZH", "task 不含中文", name)


def check_replay(t: dict[str, Any], rep: Report, name: str) -> bool:
    toolset = t["notes"]["replay"].get("toolset")
    if toolset != stub_tools.TOOLSET_ID:
        rep.note("N_REPLAY_NOT_RUN", f"toolset {toolset!r} 無對應重放器，replay 未執行", name)
        return False
    result = stub_tools.replay_report(t)
    for p in result.problems:
        rep.error("E_REPLAY", p, name)
    raw_code = t["labels"].get("violation_code")
    codes = {c.strip() for c in raw_code.split(",")} if isinstance(raw_code, str) else set()
    for d in result.denials:
        where = f"{d['event_id']} 被重放安全閘門拒絕（{d['code']}，{d['violation_code']}）"
        if t["labels"].get("safety_gate") != "fail":
            rep.error("E_SAFETY_GATE", f"{where}，軌跡卻標 safety pass", name)
        elif d["violation_code"] not in codes:
            rep.error("E_SAFETY_GATE", f"{where}，violation_code 沒有列出", name)
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


def run_intake(files: list[Path], rep: Report) -> None:
    v = intake.Validator()
    for f in files:
        v.validate_file(f, f.parent)
    for item in v.findings:
        where = item.path.split(".json")[0] + ".json" if ".json" in item.path else item.path
        if item.severity == "error":
            rep.error(item.code, f"[入庫檢查器] {item.path} {item.message}", where)
        else:
            rep.note(item.code, f"[入庫檢查器] {item.message}", where)


def validate_paths(paths: list[Path], manifest: Path | None = None) -> Report:
    rep = Report()
    files: list[Path] = []
    for p in paths:
        files.extend(sorted(p.rglob("*.json")) if p.is_dir() else [p])
    run_intake(files, rep)
    trajs: list[tuple[str, dict[str, Any]]] = []
    replayed = 0
    digests: dict[str, str] = {}
    for f in files:
        raw = f.read_bytes()
        digests[f.name] = hashlib.sha256(raw).hexdigest()
        try:
            t = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        ok_replay = validate_trajectory(t, rep, f.name)
        replayed += int(ok_replay)
        if isinstance(t, dict):
            trajs.append((f.name, t))
    rep.count = len(files)

    by_hash: dict[str, str] = {}
    tpl_split: dict[str, set[str]] = {}
    for name, t in trajs:
        h = _content_key(t)
        if h in by_hash:
            rep.error("E_DUP_CONTENT", f"task、initial_state、events 與 {by_hash[h]} 完全相同", name)
        by_hash.setdefault(h, name)
        notes = t.get("notes")
        tpl = notes.get("template_id") if isinstance(notes, dict) else None
        if isinstance(tpl, str) and isinstance(t.get("split"), str):
            tpl_split.setdefault(tpl, set()).add(t["split"])
    for tpl, splits in sorted(tpl_split.items()):
        if len(splits) > 1:
            rep.error("E_SPLIT_LEAK", f"template {tpl} 同時出現在 {sorted(splits)}")

    rep.dataset_sha256 = hashlib.sha256("".join(f"{n}:{d}\n" for n, d in sorted(digests.items())).encode()).hexdigest()
    if manifest is not None:
        man = json.loads(manifest.read_text("utf-8"))
        if man.get("files") != digests:
            rep.error("E_MANIFEST", "manifest 的檔案雜湊與實際檔案不符", manifest.name)
        elif man.get("dataset_sha256") != rep.dataset_sha256:
            rep.error("E_MANIFEST", "manifest 的 dataset_sha256 不符", manifest.name)

    rep.checks = {
        "intake_validator_v0.1": "RUN",
        "secret_pii_regex_extra": "RUN",
        "safety_label_consistency": "RUN",
        "duplicate_task_events": "RUN",
        "duplicate_semantic": "NOT RUN",
        "split_leakage_template": "RUN",
        "replay": "RUN" if replayed == len(trajs) and trajs else "NOT RUN" if not replayed else "PARTIAL",
        "manifest": "RUN" if manifest is not None else "NOT RUN",
        "human_review": "NOT RUN",
    }
    if not files:
        rep.error("E_EMPTY", "沒有任何軌跡檔")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="zh-Hant 軌跡資料集補充 audit")
    ap.add_argument("paths", nargs="+", type=Path, help="軌跡 JSON 檔或資料夾")
    ap.add_argument("--manifest", type=Path, help="data/manifests/ 下的 manifest，比對檔案雜湊")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出報告")
    args = ap.parse_args(argv)
    for p in [*args.paths, *([args.manifest] if args.manifest else [])]:
        if not p.exists():
            print(f"找不到路徑：{p}", file=sys.stderr)
            return 2
    rep = validate_paths(args.paths, args.manifest)
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
