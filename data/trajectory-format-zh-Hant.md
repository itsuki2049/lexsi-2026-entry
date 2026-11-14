# zh-Hant 軌跡資料格式規範（草案 v0）

每條 trajectory 一個 JSON 檔，欄位：

- `id`：軌跡唯一 ID（traj-<YYYYMMDD>-<seq>）
- `locale`：zh-Hant-TW 或 zh-Hant-HK
- `task`：任務自然語言描述（繁體中文）
- `tool_schemas`：可用工具的 JSON schema 陣列
- `events`：依序的呼叫紀錄（tool name、arguments、result、timestamp_ms、error）
- `final_state`：可觀測的最終狀態（供 task success 判定，不接受 agent 自述）
- `budget_ms`：預先固定的時間預算（latency 評分基準）
- `labels`：task success（0-4）、tool-call accuracy（0-4）、latency（0-4）、safety gate（pass／fail＋違規代碼）

安全規則：任何 events 內容不得含真實 secret／PII（送審前過 luna 的 safety gate）。
