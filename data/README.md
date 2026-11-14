# zh-Hant 軌跡資料格式規範（`eiraku.traj/0.1`）

所有檔案為 UTF-8、JSON Lines（一行一筆），鍵名一律英文，內容文字保留原樣不做預先正規化。正式 JSON Schema 在 [`schema/`](schema/)，`evals/schema.py` 以標準函式庫實作相同檢查，`make test` 會驗證所有示範資料。

## 1. 工具定義 `tools.json`

OpenAI function calling 格式的陣列：`{"type": "function", "function": {"name", "description", "parameters"}}`。`parameters` 為 JSON Schema，harness 會檢查 `required`、`type`、`enum`、`pattern` 與未宣告引數。工具描述用 zh-Hant 撰寫。

## 2. 任務 `tasks.jsonl`（[`schema/task.schema.json`](schema/task.schema.json)）

| 欄位 | 必填 | 說明 |
|---|---|---|
| `task_id` | 是 | 唯一識別，建議 `{lang 字首}-{主題}-{序號}`，例如 `zh-weather-001` |
| `pair_id` | 是 | zh-Hant 與 en 對照題共用，語言是唯一操作變因 |
| `lang` | 是 | `zh-Hant` 或 `en` |
| `locale` | 否 | `zh-TW`、`en-US` |
| `instruction` | 是 | 使用者指令原文 |
| `tools` | 是 | 本題可用的工具名稱 |
| `reference.tool_calls` | 否 | 參考工具呼叫（name 加 arguments），空陣列代表「不應呼叫工具」 |
| `reference.answer.contains` | 否 | 最終答案正規化後必須包含的字串 |
| `reference.answer.equals` | 否 | 最終答案正規化後必須完全相等 |
| `reference.final_state` | 否 | 最終狀態必須相等的鍵值（例如行事曆是否已建立） |
| `budgets.max_steps`／`budgets.max_latency_ms` | 否 | 步數與延遲預算，用於「預算內成功率」 |
| `tags` | 否 | 語言現象標籤，見第 4 節 |

`reference` 的參考引數一律寫成工具 Schema 要求的正規形式（例如 ISO 日期、`台`、阿拉伯數字），中文題與英文題的引數值依語言各自撰寫（`台北市` 與 `Taipei`）。

## 3. 軌跡 `runs/*.jsonl`（[`schema/trajectory.schema.json`](schema/trajectory.schema.json)）

一行是一個 episode（一個模型對一題的一次執行）。

| 欄位 | 說明 |
|---|---|
| `schema_version` | 固定 `eiraku.traj/0.1` |
| `run_id` | 同一次實驗設定（模型、prompt、介入、seed）共用 |
| `task_id` | 對應 `tasks.jsonl` |
| `model.id`／`model.revision` | 模型名稱與固定版本（Hugging Face commit 或 API 版本字串） |
| `seed` | 取樣種子 |
| `status` | `completed`、`error`、`timeout`；後兩者一律判定任務失敗（verdict `error`） |
| `events` | 事件陣列，依時間排序 |
| `final_answer` | 給使用者的最終回答 |
| `final_state` | 環境最終狀態（有狀態工具才需要） |
| `provenance` | 產生方式，例如 `agenttune`、`handwritten`，以及上游 `lexsi_provenance.json` 的 checksum |

事件共同欄位：`type`、`t_start_ms`、`t_end_ms`（以 episode 起點為 0 的毫秒數，`t_end_ms >= t_start_ms`，事件起點不得早於前一事件起點）。

| `type` | 額外欄位 | 計入延遲 |
|---|---|---|
| `reasoning` | `text` | 模型時間 |
| `message` | `text` | 模型時間 |
| `tool_call` | `call_id`、`name`、`arguments`（物件，保留模型原始輸出） | 模型時間 |
| `tool_result` | `call_id`（必須對應先前的 `tool_call`）、`output`、`error` | 工具時間 |
| `final` | `text` | 模型時間 |

與 AgentTune `EventLog` 的對應（`evals/adapters/agenttune.py`）：`tool_call` 對 `kind="tool_call"`、`tool_result` 對 `kind="tool_result"`、`reasoning` 對 `kind="reasoning"`，`message` 與 `final` 對 `kind="text"`。

## 4. zh-Hant 語言現象標籤

| 標籤 | 意義 | `zh_norm` 是否吸收 |
|---|---|---|
| `variant:臺台` | 臺／台等異體字 | 是 |
| `date:roc` | 民國紀年（民國 115 年 = 2026 年） | 是 |
| `date:slash` | `2026/11/20` | 是 |
| `date:gregorian_cjk` | `2026 年 11 月 15 日` | 是 |
| `number:thousands` | `3,000` 千分位 | 是 |
| `fullwidth` | 全形英數與標點 | 是 |
| `time:12h_zh` | 「下午3點」 | 否（必須由模型或執行層轉成 `15:00`） |
| `recovery:schema_error` | 預期出現 Schema 錯誤後修正 | 不適用 |
| `script:simplified_leak` | 回答可能混入簡體字 | 不適用，另計簡體洩漏率 |

## 5. 正規化規則（`zh_norm`，`evals/normalize.py`）

依序：Unicode NFKC（全形轉半形）、中文標點轉半形、異體字（臺→台）、日期轉 ISO 8601（民國年加 1911）、移除千分位、壓縮空白、casefold。數值字串轉成數字後比較，`3000` 與 `3000.0` 視為相同。`zh_norm` 只處理表層格式，不做翻譯，所以跨語言比較一律在 `pair_id` 層級比指標，不直接比引數。

## 6. 繁簡檢查

`simplified_chars()` 以一份常見簡體專用字清單做快速檢查，只對 `lang = zh-Hant` 的最終答案計算「簡體洩漏率」。這是啟發式，正式資料集建議再以 OpenCC（`opencc -c s2twp`）比對，有差異的樣本人工確認。

## 7. 可重現要求

- 每份 `runs/*.jsonl` 必須能追溯到產生它的模型版本、seed 與程式 commit。
- 評測報告記錄所有輸入檔的 SHA-256（`inputs_sha256`）。
- 手寫或合成資料一律在 `provenance.generated_by` 註明，不得與模型輸出混在同一個 `run_id`。

## 8. 與草案 v0（[`trajectory-format-zh-Hant.md`](trajectory-format-zh-Hant.md)）的對應

| 草案 v0 | `eiraku.traj/0.1` |
|---|---|
| `id` | `run_id` 加 `task_id` |
| `locale` | 任務的 `locale` |
| `task` | 任務的 `instruction` |
| `tool_schemas` | `tools.json` 與任務的 `tools` |
| `events`（`timestamp_ms`） | `events`（`t_start_ms`、`t_end_ms`） |
| `final_state` | `final_state` |
| `budget_ms` | 任務的 `budgets.max_latency_ms` |
| `labels`（0 至 4 分與 safety gate） | 尚未納入，judge 結果建議另存一份以 `run_id`、`task_id` 為鍵的 labels JSONL |
