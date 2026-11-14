"""繁體中文（台灣）參數與答案正規化。

正規化只用於 `zh_norm` 比對模式；`exact` 模式一律比對原始 JSON 值。
兩種模式的分數差，就是「表層形式」造成的落差。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# 異體字：只收台灣日常文件中可互換、且不改變語意的字。
VARIANTS = {"臺": "台"}

# 中文標點轉半形，NFKC 不會處理的部分。
PUNCT = {"、": ",", "。": ".", "「": '"', "」": '"', "『": '"', "』": '"', "～": "~"}

_ROC_DATE = re.compile(r"(?:中華)?民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_ISO_SLASH = re.compile(r"\b(\d{4})[/.](\d{1,2})[/.](\d{1,2})\b")
_CJK_DATE = re.compile(r"\b(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_SPACE = re.compile(r"\s+")

ROC_OFFSET = 1911


def _iso(y: int, m: int, d: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"


def normalize_dates(text: str) -> str:
    """民國年、斜線日期與「YYYY年M月D日」一律轉成 ISO 8601（YYYY-MM-DD）。"""
    text = _ROC_DATE.sub(lambda m: _iso(int(m[1]) + ROC_OFFSET, int(m[2]), int(m[3])), text)
    text = _CJK_DATE.sub(lambda m: _iso(int(m[1]), int(m[2]), int(m[3])), text)
    return _ISO_SLASH.sub(lambda m: _iso(int(m[1]), int(m[2]), int(m[3])), text)


def normalize_text(text: str) -> str:
    """NFKC（全形轉半形）、標點、異體字、日期、千分位與空白。"""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(PUNCT.get(ch, VARIANTS.get(ch, ch)) for ch in text)
    text = normalize_dates(text)
    text = _THOUSANDS.sub("", text)
    return _SPACE.sub(" ", text).strip().casefold()


def _maybe_number(text: str) -> Any:
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def normalize_value(value: Any) -> Any:
    """遞迴正規化 JSON 值；字串若正規化後是數字，視為數字。"""
    if isinstance(value, str):
        return _maybe_number(normalize_text(value))
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else float(value)
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    if isinstance(value, dict):
        return {normalize_text(str(k)): normalize_value(v) for k, v in value.items()}
    return value


# 簡體專用字：繁體文本中幾乎不會出現的常用字，用來偵測簡體洩漏。
# 這是輕量的啟發式檢查；完整檢查請用 OpenCC（見 data/README.md）。
SIMPLIFIED_ONLY = set(
    "这们说时个来对国发会为经过还进动关实现点问题应该开将气区价统计处视数据认识"
    "东车马门间页书长银铁见语读写电话钟币汇圆总无给单队乐网飞机场览档简体"
)


def simplified_chars(text: str) -> list[str]:
    return sorted({ch for ch in text if ch in SIMPLIFIED_ONLY})
