#!/usr/bin/env python3
"""Validate Lexsi zh-Hant trajectory JSON files before dataset intake.

This checker never executes a tool, imports a trajectory, or writes into the
dataset. It validates the proposed v0.1 intake contract and reports sensitive
match locations without echoing matched values. Manual language, provenance,
oracle, and safety review remains mandatory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 40
SPLITS = {"train", "validation", "test", "holdout"}
LOCALES = {"zh-Hant-TW", "zh-Hant-HK"}
LABEL_KEYS = {"task_success", "tool_call_accuracy", "latency", "safety_gate", "violation_code", "safety_review"}
ROOT_KEYS = {
    "schema_version", "id", "locale", "task", "initial_state", "expected_state",
    "permissions", "tool_schemas", "events", "final_state", "budget_ms", "elapsed_ms",
    "labels", "labels_metadata", "provenance", "split", "group_id", "notes",
}

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.I)
AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
GITHUB_TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")
OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
SECRET_CONTEXT_RE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|secret|credential)"
    r"\s*[:=]\s*[\"']?([^\s\"',;}]{12,})",
    re.I,
)
HKID_RE = re.compile(r"\b[A-Z]{1,2}\d{6}\([0-9A]\)\b", re.I)
TWID_RE = re.compile(r"\b[A-Z][12]\d{8}\b", re.I)
PHONE_CONTEXT_RE = re.compile(r"(?:電話|手機|手機號|電話號碼|聯絡電話|mobile|phone|tel)\D{0,12}", re.I)
PHONE_RE = re.compile(r"(?:\+?852[- ]?)?[2-9]\d{3}[- ]?\d{4}|(?:\+?886[- ]?)?09\d{2}[- ]?\d{3}[- ]?\d{3}")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
SAFE_PLACEHOLDERS = {
    "redacted", "placeholder", "your_api_key", "replace_me", "example_token",
    "not-a-real-token", "fake-token", "test-secret", "<secret>", "[redacted]",
}
RESERVED_EMAIL_DOMAINS = {"example.com", "example.net", "example.org", "invalid", "test"}


@dataclass
class Finding:
    severity: str
    path: str
    code: str
    message: str


class Validator:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.ids: dict[str, str] = {}
        self.fingerprints: dict[str, str] = {}
        self.group_splits: dict[str, tuple[str, str]] = {}
        self.files = 0

    def add(self, severity: str, path: str, code: str, message: str) -> None:
        self.findings.append(Finding(severity, path, code, message))

    def walk_strings(self, value: Any, path: str = "$") -> None:
        if isinstance(value, str):
            self.scan_string(value, path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self.walk_strings(item, f"{path}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                self.scan_string(str(key), f"{path}.<key>")
                self.walk_strings(item, f"{path}.{key}")

    def scan_string(self, text: str, path: str) -> None:
        low = text.strip().lower()
        if low in SAFE_PLACEHOLDERS or (low.startswith("<") and low.endswith(">")):
            return
        patterns: list[tuple[re.Pattern[str], str]] = [
            (PRIVATE_KEY_RE, "private_key_like"), (AWS_KEY_RE, "aws_access_key_like"),
            (GITHUB_TOKEN_RE, "github_token_like"), (SLACK_TOKEN_RE, "slack_token_like"),
            (OPENAI_STYLE_KEY_RE, "api_key_like"), (JWT_RE, "jwt_like"), (HKID_RE, "hkid_like"),
            (TWID_RE, "twid_like"),
        ]
        for pattern, code in patterns:
            if pattern.search(text):
                self.add("error", path, "sensitive_" + code, "疑似敏感憑證或識別碼，需隔離人工覆核")
        for match in EMAIL_RE.finditer(text):
            domain = match.group(1).lower()
            if domain not in RESERVED_EMAIL_DOMAINS and not domain.endswith((".example", ".test", ".invalid")):
                self.add("error", path, "sensitive_email_like", "疑似非保留網域電子郵件")
                break
        if PHONE_CONTEXT_RE.search(text) and PHONE_RE.search(text):
            self.add("error", path, "sensitive_phone_like", "電話欄位含疑似電話號碼")
        if self._contains_luhn_card(text):
            self.add("error", path, "sensitive_card_like", "疑似通過 Luhn 檢查的卡號")
        for match in SECRET_CONTEXT_RE.finditer(text):
            candidate = match.group(1).strip().lower().rstrip(".]")
            if candidate not in SAFE_PLACEHOLDERS and not candidate.startswith(("<", "[redacted")):
                self.add("error", path, "sensitive_secret_context", "憑證欄位含非 placeholder 值")
                break

    @staticmethod
    def _contains_luhn_card(text: str) -> bool:
        for match in CARD_RE.finditer(text):
            digits = "".join(ch for ch in match.group(0) if ch.isdigit())
            if not 13 <= len(digits) <= 19:
                continue
            total = 0
            parity = len(digits) % 2
            for index, char in enumerate(digits):
                number = int(char)
                if index % 2 == parity:
                    number *= 2
                    if number > 9:
                        number -= 9
                total += number
            if total % 10 == 0:
                return True
        return False

    def validate_tree(self, root: Path) -> None:
        if root.is_symlink() or not root.is_dir():
            self.add("error", str(root), "invalid_dataset_dir", "資料路徑必須是非 symlink 目錄")
            return
        paths: list[Path] = []
        for current, dirnames, filenames in __import__("os").walk(root, followlinks=False):
            current_path = Path(current)
            for name in list(dirnames):
                candidate = current_path / name
                if candidate.is_symlink():
                    self.add("error", str(candidate), "symlink_dir", "資料集不可包含 symlink 目錄")
                    dirnames.remove(name)
            for name in filenames:
                if name.lower().endswith(".json"):
                    paths.append(current_path / name)
        if not paths:
            self.add("error", str(root), "no_json_files", "找不到 trajectory JSON 檔")
            return
        for path in sorted(paths):
            self.validate_file(path, root)
        for group, (split, first_path) in self.group_splits.items():
            del group, split, first_path

    def validate_file(self, path: Path, root: Path) -> None:
        self.files += 1
        relative = str(path.relative_to(root))
        if path.is_symlink():
            self.add("error", relative, "symlink_file", "資料集不可包含 symlink 檔案")
            return
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                self.add("error", relative, "file_too_large", f"檔案超過 {MAX_FILE_BYTES} bytes")
                return
            raw = path.read_bytes()
            data = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            self.add("error", relative, "invalid_json", f"無法安全解析 JSON：{type(exc).__name__}")
            return
        if self._depth(data) > MAX_DEPTH:
            self.add("error", relative, "json_too_deep", f"JSON 巢狀深度超過 {MAX_DEPTH}")
            return
        self.walk_strings(data, relative)
        if not isinstance(data, dict):
            self.add("error", relative, "root_not_object", "每個檔案的 JSON 根節點必須是 object")
            return
        self.validate_record(data, relative)

    @staticmethod
    def _depth(value: Any, depth: int = 0) -> int:
        if isinstance(value, dict):
            return max([depth] + [Validator._depth(v, depth + 1) for v in value.values()])
        if isinstance(value, list):
            return max([depth] + [Validator._depth(v, depth + 1) for v in value])
        return depth

    def validate_record(self, record: dict[str, Any], path: str) -> None:
        required = {
            "schema_version", "id", "locale", "task", "initial_state", "expected_state",
            "permissions", "tool_schemas", "events", "final_state", "budget_ms", "elapsed_ms",
            "labels", "labels_metadata", "provenance", "split", "group_id",
        }
        for key in sorted(required - record.keys()):
            self.add("error", path, "missing_field", f"缺少 v0.1 必要欄位：{key}")
        for key in sorted(record.keys() - ROOT_KEYS):
            self.add("error", f"{path}.{key}", "unreviewed_field", "未登記欄位，需先納入 schema review")
        if record.get("schema_version") != "0.1":
            self.add("error", f"{path}.schema_version", "schema_version", "schema_version 必須為字串 0.1")
        self._validate_id(record.get("id"), path)
        self._validate_locale(record.get("locale"), path)
        self._validate_text(record.get("task"), f"{path}.task")
        for key in ("initial_state", "expected_state", "final_state"):
            if key in record and not isinstance(record[key], dict):
                self.add("error", f"{path}.{key}", "state_not_object", "狀態欄位必須是 object")
        self._validate_time(record.get("budget_ms"), f"{path}.budget_ms", positive=True)
        self._validate_time(record.get("elapsed_ms"), f"{path}.elapsed_ms", positive=False)
        tools = self._validate_tools(record.get("tool_schemas"), f"{path}.tool_schemas")
        self._validate_permissions(record.get("permissions"), tools, f"{path}.permissions")
        self._validate_events(record.get("events"), tools, record.get("elapsed_ms"), f"{path}.events")
        self._validate_labels(record.get("labels"), f"{path}.labels")
        self._validate_label_metadata(record.get("labels_metadata"), f"{path}.labels_metadata")
        self._validate_provenance(record.get("provenance"), f"{path}.provenance")
        split = record.get("split")
        group_id = record.get("group_id")
        if not isinstance(split, str) or split not in SPLITS:
            self.add("error", f"{path}.split", "invalid_split", "split 必須為 train、validation、test 或 holdout")
        if not isinstance(group_id, str) or not group_id.strip():
            self.add("error", f"{path}.group_id", "invalid_group_id", "group_id 必須是非空字串")
        elif split in SPLITS:
            previous = self.group_splits.get(group_id)
            if previous and previous[0] != split:
                self.add("error", f"{path}.group_id", "split_leakage", f"同一 group_id 已出現在 split {previous[0]} 的 {previous[1]}")
            else:
                self.group_splits[group_id] = (split, path)
        self._check_fingerprint(record, path)

    def _validate_id(self, value: Any, path: str) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"traj-\d{8}-\d{3,}", value):
            self.add("error", f"{path}.id", "invalid_id", "id 格式須為 traj-YYYYMMDD-至少三位序號")
            return
        try:
            datetime.strptime(value[5:13], "%Y%m%d")
        except ValueError:
            self.add("error", f"{path}.id", "invalid_id_date", "id 日期不是有效日曆日期")
        if value in self.ids:
            self.add("error", f"{path}.id", "duplicate_id", f"id 已出現在 {self.ids[value]}")
        else:
            self.ids[value] = path

    def _validate_locale(self, value: Any, path: str) -> None:
        if not isinstance(value, str) or value not in LOCALES:
            self.add("error", f"{path}.locale", "invalid_locale", "locale 必須為 zh-Hant-TW 或 zh-Hant-HK")

    def _validate_text(self, value: Any, path: str) -> None:
        if not isinstance(value, str) or not value.strip():
            self.add("error", path, "empty_text", "欄位必須是非空字串")

    def _validate_time(self, value: Any, path: str, positive: bool) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
            expected = "正整數" if positive else "非負整數"
            self.add("error", path, "invalid_duration", f"欄位必須是{expected}毫秒")

    def _validate_tools(self, value: Any, path: str) -> dict[str, dict[str, Any]]:
        tools: dict[str, dict[str, Any]] = {}
        if not isinstance(value, list) or not value:
            self.add("error", path, "invalid_tools", "tool_schemas 必須是非空 array")
            return tools
        for index, entry in enumerate(value):
            item_path = f"{path}[{index}]"
            if not isinstance(entry, dict):
                self.add("error", item_path, "invalid_tool", "工具定義必須是 object")
                continue
            name = entry.get("name")
            schema = entry.get("parameters")
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,63}", name):
                self.add("error", f"{item_path}.name", "invalid_tool_name", "工具 name 格式錯誤")
                continue
            if name in tools:
                self.add("error", f"{item_path}.name", "duplicate_tool", "tool name 重複")
            if not isinstance(schema, dict):
                self.add("error", f"{item_path}.parameters", "missing_tool_schema", "每個工具須提供 parameters JSON Schema")
                continue
            self._validate_schema_definition(schema, f"{item_path}.parameters", 0)
            if schema.get("type") != "object":
                self.add("error", f"{item_path}.parameters.type", "tool_args_not_object", "工具參數根 schema type 必須是 object")
            if schema.get("additionalProperties") is not False:
                self.add("error", f"{item_path}.parameters.additionalProperties", "tool_schema_not_closed", "工具參數 schema 必須明確設定 additionalProperties=false")
            tools[name] = schema
        return tools

    def _validate_schema_definition(self, schema: Any, path: str, depth: int) -> None:
        if depth > 24:
            self.add("error", path, "schema_too_deep", "工具 JSON Schema 巢狀過深")
            return
        if not isinstance(schema, dict):
            self.add("error", path, "invalid_schema", "JSON Schema 節點必須是 object")
            return
        if "$ref" in schema or "oneOf" in schema or "anyOf" in schema or "allOf" in schema:
            self.add("error", path, "unsupported_schema_keyword", "v0.1 checker 不支援 $ref/oneOf/anyOf/allOf")
        allowed_types = {"object", "array", "string", "integer", "number", "boolean", "null"}
        schema_type = schema.get("type")
        if schema_type is not None and (not isinstance(schema_type, str) or schema_type not in allowed_types):
            self.add("error", f"{path}.type", "invalid_schema_type", "JSON Schema type 不在支援範圍")
        supported = {
            "type", "properties", "required", "additionalProperties", "items", "enum", "const",
            "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum",
        }
        if set(schema) - supported:
            self.add("error", path, "unsupported_schema_keyword", "JSON Schema 含未支援關鍵字，v0.1 只接受明列子集")
        properties = schema.get("properties", {})
        if properties is not None and not isinstance(properties, dict):
            self.add("error", f"{path}.properties", "invalid_schema_properties", "properties 必須是 object")
        elif isinstance(properties, dict):
            for key, child in properties.items():
                self._validate_schema_definition(child, f"{path}.properties.{key}", depth + 1)
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(v, str) for v in required):
            self.add("error", f"{path}.required", "invalid_schema_required", "required 必須是字串 array")
        elif isinstance(properties, dict) and not set(required).issubset(properties):
            self.add("error", f"{path}.required", "unknown_required_property", "required 含 properties 未定義欄位")
        for key in ("minItems", "maxItems", "minLength", "maxLength"):
            if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], int) or schema[key] < 0):
                self.add("error", f"{path}.{key}", "invalid_schema_bound", f"{key} 必須是非負整數")
        for key in ("minimum", "maximum"):
            if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))):
                self.add("error", f"{path}.{key}", "invalid_schema_bound", f"{key} 必須是數值")
        if isinstance(schema.get("minItems"), int) and isinstance(schema.get("maxItems"), int) and schema["minItems"] > schema["maxItems"]:
            self.add("error", path, "inverted_schema_bounds", "minItems 不可大於 maxItems")
        if isinstance(schema.get("minLength"), int) and isinstance(schema.get("maxLength"), int) and schema["minLength"] > schema["maxLength"]:
            self.add("error", path, "inverted_schema_bounds", "minLength 不可大於 maxLength")
        if isinstance(schema.get("minimum"), (int, float)) and isinstance(schema.get("maximum"), (int, float)) and schema["minimum"] > schema["maximum"]:
            self.add("error", path, "inverted_schema_bounds", "minimum 不可大於 maximum")
        items = schema.get("items")
        if items is not None:
            self._validate_schema_definition(items, f"{path}.items", depth + 1)

    def _validate_permissions(self, value: Any, tools: dict[str, dict[str, Any]], path: str) -> None:
        if not isinstance(value, dict):
            self.add("error", path, "invalid_permissions", "permissions 必須是 object")
            return
        allowed = value.get("allowed_tools")
        if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
            self.add("error", f"{path}.allowed_tools", "invalid_allowed_tools", "allowed_tools 必須是字串 array")
        elif len(allowed) != len(set(allowed)) or set(allowed) != set(tools):
            self.add("error", f"{path}.allowed_tools", "tool_allowlist_mismatch", "allowed_tools 須唯一且與 tool_schemas 名稱集合一致")
        if not isinstance(value.get("human_approval_required"), list):
            self.add("error", f"{path}.human_approval_required", "invalid_approval_policy", "human_approval_required 必須列出需人工核准的操作類別")
        side_effects = value.get("side_effects")
        if not isinstance(side_effects, list) or any(not isinstance(v, str) for v in side_effects):
            self.add("error", f"{path}.side_effects", "invalid_side_effects", "side_effects 必須是字串 array")

    def _validate_events(self, value: Any, tools: dict[str, dict[str, Any]], elapsed: Any, path: str) -> None:
        if not isinstance(value, list):
            self.add("error", path, "invalid_events", "events 必須是 array")
            return
        previous_time = -1
        for index, event in enumerate(value):
            item_path = f"{path}[{index}]"
            if not isinstance(event, dict):
                self.add("error", item_path, "invalid_event", "event 必須是 object")
                continue
            if event.get("seq") != index:
                self.add("error", f"{item_path}.seq", "invalid_event_seq", "seq 必須從 0 開始連續遞增")
            tool_name = event.get("tool_name")
            if not isinstance(tool_name, str) or tool_name not in tools:
                self.add("error", f"{item_path}.tool_name", "unknown_tool", "event tool_name 不在 tool_schemas")
            args = event.get("arguments")
            if not isinstance(args, dict):
                self.add("error", f"{item_path}.arguments", "invalid_arguments", "arguments 必須是 object")
            elif tool_name in tools:
                self._validate_schema_value(args, tools[tool_name], f"{item_path}.arguments", 0)
            if "result" not in event:
                self.add("error", f"{item_path}.result", "missing_result", "每個完成的工具呼叫都必須有 result 欄位，可用 null 表示空結果")
            timestamp = event.get("timestamp_ms")
            if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
                self.add("error", f"{item_path}.timestamp_ms", "invalid_timestamp", "timestamp_ms 必須是相對任務開始的非負整數毫秒")
            else:
                if timestamp < previous_time:
                    self.add("error", f"{item_path}.timestamp_ms", "non_monotonic_time", "timestamp_ms 必須單調不減")
                previous_time = timestamp
                if isinstance(elapsed, int) and timestamp > elapsed:
                    self.add("error", f"{item_path}.timestamp_ms", "timestamp_after_elapsed", "事件時間不可晚於 elapsed_ms")
            if "error" not in event:
                self.add("error", f"{item_path}.error", "missing_error_field", "error 欄位必須存在，無錯誤時填 null")
            if not isinstance(event.get("trust_level"), str) or event.get("trust_level") not in {"trusted", "untrusted"}:
                self.add("error", f"{item_path}.trust_level", "invalid_trust_level", "工具回傳須標 trusted 或 untrusted，外部來源預設 untrusted")

    def _validate_schema_value(self, value: Any, schema: dict[str, Any], path: str, depth: int) -> None:
        if depth > 24:
            self.add("error", path, "value_too_deep", "工具參數巢狀過深")
            return
        expected_type = schema.get("type")
        type_ok = {
            "object": lambda x: isinstance(x, dict),
            "array": lambda x: isinstance(x, list),
            "string": lambda x: isinstance(x, str),
            "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
            "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
            "boolean": lambda x: isinstance(x, bool),
            "null": lambda x: x is None,
        }
        if expected_type in type_ok and not type_ok[expected_type](value):
            self.add("error", path, "tool_argument_type", f"參數型別不符，預期 {expected_type}")
            return
        if "enum" in schema:
            if not isinstance(schema["enum"], list):
                self.add("error", path, "invalid_schema_enum", "enum 必須是 array")
            elif value not in schema["enum"]:
                self.add("error", path, "tool_argument_enum", "參數值不在 enum")
        if "const" in schema and value != schema["const"]:
            self.add("error", path, "tool_argument_const", "參數值不符合 const")
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            if not isinstance(properties, dict):
                return
            for key in schema.get("required", []):
                if key not in value:
                    self.add("error", f"{path}.{key}", "missing_tool_argument", "缺少 schema required 參數")
            if schema.get("additionalProperties") is False:
                for key in value.keys() - properties.keys():
                    self.add("error", f"{path}.{key}", "unexpected_tool_argument", "additionalProperties=false，不接受額外參數")
            for key, item in value.items():
                if key in properties:
                    self._validate_schema_value(item, properties[key], f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            if isinstance(schema.get("minItems", 0), int) and not isinstance(schema.get("minItems", 0), bool) and len(value) < schema.get("minItems", 0):
                self.add("error", path, "array_too_short", "array 少於 minItems")
            if isinstance(schema.get("maxItems"), int) and not isinstance(schema.get("maxItems"), bool) and len(value) > schema["maxItems"]:
                self.add("error", path, "array_too_long", "array 超過 maxItems")
            if isinstance(schema.get("items"), dict):
                for index, item in enumerate(value):
                    self._validate_schema_value(item, schema["items"], f"{path}[{index}]", depth + 1)
        elif isinstance(value, str):
            if isinstance(schema.get("minLength", 0), int) and not isinstance(schema.get("minLength", 0), bool) and len(value) < schema.get("minLength", 0):
                self.add("error", path, "string_too_short", "string 少於 minLength")
            if isinstance(schema.get("maxLength"), int) and not isinstance(schema.get("maxLength"), bool) and len(value) > schema["maxLength"]:
                self.add("error", path, "string_too_long", "string 超過 maxLength")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
                self.add("error", path, "number_below_minimum", "數值低於 minimum")
            if isinstance(schema.get("maximum"), (int, float)) and value > schema["maximum"]:
                self.add("error", path, "number_above_maximum", "數值高於 maximum")

    def _validate_labels(self, value: Any, path: str) -> None:
        if not isinstance(value, dict):
            self.add("error", path, "invalid_labels", "labels 必須是 object")
            return
        if value.keys() != LABEL_KEYS:
            self.add("error", path, "label_keys", "labels 必須剛好包含三項 0 至 4 分數、safety_gate 與 violation_code")
        for key in ("task_success", "tool_call_accuracy", "latency"):
            score = value.get(key)
            if isinstance(score, bool) or not isinstance(score, int) or score not in range(5):
                self.add("error", f"{path}.{key}", "invalid_score", "分數必須為整數 0 至 4")
        gate = value.get("safety_gate")
        code = value.get("violation_code")
        if not isinstance(gate, str) or gate not in {"pass", "fail"}:
            self.add("error", f"{path}.safety_gate", "invalid_safety_gate", "safety_gate 必須為 pass 或 fail")
        elif gate == "pass" and code not in (None, ""):
            self.add("error", f"{path}.violation_code", "unexpected_violation_code", "safety_gate pass 時 violation_code 須為 null")
        elif gate == "fail" and (not isinstance(code, str) or not code.strip()):
            self.add("error", f"{path}.violation_code", "missing_violation_code", "safety_gate fail 時須提供違規代碼")
        review = value.get("safety_review")
        if not isinstance(review, dict):
            self.add("error", f"{path}.safety_review", "invalid_safety_review", "需記錄安全風險與允許行為、禁止行為、safe alternative、人工確認要求")
        else:
            for key in ("risk_category", "severity", "safe_alternative"):
                if not isinstance(review.get(key), str) or not review[key].strip():
                    self.add("error", f"{path}.safety_review.{key}", "missing_safety_review_field", f"safety_review.{key} 必須是非空字串")
            for key in ("allowed_behaviors", "prohibited_behaviors"):
                if not isinstance(review.get(key), list) or any(not isinstance(item, str) for item in review.get(key, [])):
                    self.add("error", f"{path}.safety_review.{key}", "invalid_safety_behavior_list", f"safety_review.{key} 必須是字串陣列")
            if not isinstance(review.get("human_confirmation_required"), bool):
                self.add("error", f"{path}.safety_review.human_confirmation_required", "invalid_human_confirmation", "人工確認要求必須是 boolean")

    def _validate_label_metadata(self, value: Any, path: str) -> None:
        if not isinstance(value, dict):
            self.add("error", path, "invalid_label_metadata", "labels_metadata 必須是 object")
            return
        source = value.get("source")
        if not isinstance(source, str) or source not in {"human", "llm", "adjudicated"}:
            self.add("error", f"{path}.source", "invalid_label_source", "source 必須是 human、llm 或 adjudicated")
        if not isinstance(value.get("rubric_version"), str) or not value["rubric_version"].strip():
            self.add("error", f"{path}.rubric_version", "missing_rubric_version", "需記錄 rubric version")
        if source == "llm" and not isinstance(value.get("judge_model"), str):
            self.add("error", f"{path}.judge_model", "missing_judge_model", "LLM label 須記錄 judge_model")
        if source in {"human", "adjudicated"} and not isinstance(value.get("labeler_id"), str):
            self.add("error", f"{path}.labeler_id", "missing_labeler_id", "人工標籤須使用匿名 labeler_id")

    def _validate_provenance(self, value: Any, path: str) -> None:
        if not isinstance(value, dict):
            self.add("error", path, "invalid_provenance", "provenance 必須是 object")
            return
        source_type = value.get("source_type")
        if not isinstance(source_type, str) or source_type not in {"synthetic", "recorded", "public"}:
            self.add("error", f"{path}.source_type", "invalid_source_type", "source_type 必須是 synthetic、recorded 或 public")
        for key in ("source_id", "license", "created_at"):
            self._validate_text(value.get(key), f"{path}.{key}")
        created_at = value.get("created_at")
        if isinstance(created_at, str):
            try:
                parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone required")
            except ValueError:
                self.add("error", f"{path}.created_at", "invalid_created_at", "created_at 須是含時區的 ISO 8601 timestamp")
        if source_type == "recorded" and not isinstance(value.get("consent_basis"), str):
            self.add("error", f"{path}.consent_basis", "missing_consent_basis", "recorded source 須說明 consent basis")
        if source_type == "synthetic" and not isinstance(value.get("generator"), dict):
            self.add("error", f"{path}.generator", "missing_generator", "synthetic source 須記錄 generator metadata")

    def _check_fingerprint(self, record: dict[str, Any], path: str) -> None:
        payload = dict(record)
        for key in ("id", "labels", "labels_metadata", "provenance", "notes"):
            payload.pop(key, None)
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if digest in self.fingerprints:
            self.add("error", path, "duplicate_trajectory", f"軌跡內容與 {self.fingerprints[digest]} 重複（不回顯資料）")
        else:
            self.fingerprints[digest] = path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", nargs="?", default="data/trajectories", help="含每條 trajectory JSON 的目錄")
    parser.add_argument("--json-report", type=Path, help="另存不含匹配敏感值的機器可讀報告")
    args = parser.parse_args()
    root = Path(args.dataset_dir)
    validator = Validator()
    validator.validate_tree(root)

    errors = [f for f in validator.findings if f.severity == "error"]
    warnings = [f for f in validator.findings if f.severity == "warning"]
    print(f"files={validator.files} errors={len(errors)} warnings={len(warnings)}")
    for finding in validator.findings:
        print(f"{finding.severity.upper()} {finding.path} [{finding.code}] {finding.message}")
    print("MANUAL_REVIEW_REQUIRED locale_naturalness license_validity oracle_correctness tool_safety prompt_injection_labels")

    if args.json_report:
        report = {
            "files": validator.files,
            "errors": len(errors),
            "warnings": len(warnings),
            "findings": [finding.__dict__ for finding in validator.findings],
            "manual_review_required": ["locale_naturalness", "license_validity", "oracle_correctness", "tool_safety", "prompt_injection_labels"],
        }
        args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
