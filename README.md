# EIRAKU（永樂）Lexsi AI Safety & Alignment Hackathon 2026：T3 參賽專案

**Claim：同一個 agent 在繁體中文（台灣）工具呼叫任務上的工具呼叫正確率低於英文對照題，落差主要來自表層格式（臺／台、民國年、千分位、12 小時制）而非任務理解，在執行邊界加入 zh-Hant 正規化可在不超出延遲預算的情況下收回大部分落差。**

> 目前狀態：Round 1 骨架。上面的 claim 是待驗證的假說，`data/samples/runs/demo-handwritten.jsonl` 是手寫示範軌跡，不是模型輸出，任何數字都不能當成實驗結果引用。

## 賽道

T3 agentic autonomy：量測 agent 的 tool-call 軌跡、任務成功率與延遲。語言差異化：繁體中文 zh-Hant（台灣語境，locale `zh-TW`）。

## 方法概述

1. **成對任務**：每一題都有 zh-Hant 與英文兩個版本，共用 `pair_id`、工具與 oracle，語言是唯一的操作變因。中文題刻意放入台灣使用者真實會打的格式：臺／台異體、民國紀年、`2026/11/20` 斜線日期、`3,000` 千分位、「下午3點」。
2. **軌跡收集**：以 [AgentTune](https://github.com/Lexsi-Labs/AgentTune) 的 `Project.collect()` 產生 `EventLog`，透過 `evals/adapters/agenttune.py` 轉成本專案的 `eiraku.traj/0.1` 格式（格式見 [`data/README.md`](data/README.md)）。
3. **三項主指標**（`evals/`，只用 Python 標準函式庫）：
   - **任務成功率**：以程式化 oracle 判定（最終答案包含指定字串、最終狀態鍵值相等），不使用 LLM 評審。
   - **工具呼叫正確率**：工具名稱加引數的多重集合 precision／recall／F1，分 `exact`、`zh_norm`、`name` 三種比對模式；`exact` 與 `zh_norm` 的差就是「表層格式造成的落差」。另附 Schema 合法率。
   - **延遲**：每個事件的起訖時間戳，算出總延遲、模型時間、工具時間、首次工具呼叫時間的 mean／p50／p95／max。
4. **交叉驗證**：`exact` 模式逐題對齊 [AuditKIT](https://github.com/Lexsi-Labs/AuditKIT) `ToolCallF1`（`make xcheck`），確保自製指標與官方評測 kit 一致。
5. **介入**：在工具執行邊界加入 zh-Hant 正規化層，比較介入前後的成功率、工具呼叫 F1 與延遲；並檢查繁中回答中的簡體字洩漏。

六個 Lexsi kit 與 T3 的關係見 [`docs/kit-features.md`](docs/kit-features.md)，59 小時執行計畫見 [`docs/PLAN.md`](docs/PLAN.md)。

## 重現

需要 Python 3.10 以上，核心不需安裝任何套件。

```bash
git clone https://github.com/itsuki2049/lexsi-2026-entry.git
cd lexsi-2026-entry
make test          # 單元測試與示範資料端對端測試
make demo          # 產生 results/demo.json 與 results/demo.md
```

AuditKIT 交叉驗證（選用）：

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-xcheck.txt
make xcheck PY=.venv/bin/python
```

報告會記錄每個輸入檔的 SHA-256；同樣輸入保證產生同樣報告（測試 `test_deterministic`）。

## 目錄

```text
README.md                 題目 claim 與方法
docs/kit-features.md      六個 Lexsi kit 的 T3 功能清單（固定 commit）
docs/PLAN.md              59 小時執行計畫
data/README.md            zh-Hant 軌跡資料格式規範
data/schema/              task 與 trajectory 的 JSON Schema
data/samples/             成對示範任務、工具定義、手寫示範軌跡
evals/                    評測 harness（metrics、normalize、schema、run）
evals/adapters/           AgentTune EventLog 轉接、AuditKIT 交叉驗證
evals/tests/              單元測試
```

## 授權

Apache License 2.0，見 [`LICENSE`](LICENSE)。
