# zh-Hant 軌跡格式規範 QA 複核（20261114）

審查對象：`data/trajectory-format-zh-Hant.md` 草案 v0。  
判定：**PASS WITH NOTES 作為概念草案，BLOCKED 作為可直接入庫的完整 schema。**

## 發現與建議

### NCR-DATA-001（major）：缺少可判 task success 的 oracle 與權限界線

- 發現：v0 只有 `task`、`events`、`final_state`，沒有 initial state、期望狀態或 verifier、允許工具和副作用權限。`final_state` 的欄位也未定義。
- 影響：兩個 judge 無法從同一軌跡判斷目標是否達成、是否有不允許副作用；只靠 `final_state` 命名不足以讓結果可重播或可裁決。
- 建議：增加 `initial_state`、`expected_state` 或可執行 verifier、`permissions.allowed_tools`、寫入限制、人工核准條件、每個 event 的序號與穩定 event id。支援多種合法策略，oracle 評最終狀態與限制，不要求固定唯一呼叫序列。

### NCR-DATA-002（major）：標籤與 latency 缺少來源和量測定義

- 發現：`labels` 列出三個 0 至 4 分和 safety pass/fail，但沒有 rubric 版本、標註來源、裁決狀態、違規代碼 enum、judge model 或盲測資訊。事件只有 `timestamp_ms`，未說是 wall clock、單調時鐘或相對時間，也沒有整段耗時。
- 影響：資料重標後無法追溯舊分數；不同 runner 的 `timestamp_ms` 不能直接比較，延遲分數無法重現。
- 建議：加 `labels.source`、匿名 `annotator_id` 或 judge model、`rubric_version`、`adjudication_status`。事件時間用單調遞增相對毫秒，另加整段 `elapsed_ms` 與每題 `budget_ms`，明訂 latency 只從 agent 收到任務計至最終提交。

### NCR-DATA-003（major）：缺少來源、授權、split 與分組資訊

- 發現：v0 沒有 provenance、license、synthetic/recorded 標記、consent basis、train/dev/test/holdout split 或 group id。
- 影響：無法核對資料能否公開，無法隔離個資風險，也不能檢查同一模板或任務是否洩漏到訓練與測試兩側。
- 建議：加入 `provenance`、`license`、`source_type`、必要時 `consent_basis`、`split`、`group_id` 及 dataset version。holdout red-team seeds 不得參與生成或 judge 校準。

### NCR-DATA-004（minor）：工具 schema 的封裝形狀與事件契約未定

- 發現：`tool_schemas` 稱為 JSON schema 陣列，未定義 tool name、參數 schema 的包裝方式。`events` 寫呼叫紀錄，但未定義非同步結果、重試、工具錯誤與 event 序號如何表達。
- 影響：不同產生端可能輸出不相容資料；自動檢查無法確定 call arguments 要對照哪個工具 schema。
- 建議：定義每個工具為 `{name, parameters}`，`parameters` 是 JSON Schema；每筆 event 含 `seq`、`tool_name`、`arguments`、`result`、`timestamp_ms`、`error`。一筆 event 表示一次已完成的呼叫，若要分開 call/result 則明訂 event type 和因果關聯 id。

## 可自動化範圍

入庫程式可檢查 JSON 結構、欄位型別、工具參數 schema 的常用子集、呼叫時間順序、分數域、id 唯一、exact duplicate、group split 洩漏，以及高信心 secret、email、電話、HKID/TW ID 和信用卡號型樣。程式不能證明文字自然度、license 真實有效、無姓名或住址、任務 oracle 正確、工具權限合理、prompt injection 標籤完整或軌跡行為安全；這些保留人工覆核，不可因程式 exit 0 宣稱通過。

配套工具：`scripts/validate_trajectory_dataset.py`。工具使用 v0.1 入庫契約，對 v0 未提供的審計必備欄位會阻擋資料進正式資料集。輸出只記錄檔名和欄位路徑，不回顯疑似敏感值。
