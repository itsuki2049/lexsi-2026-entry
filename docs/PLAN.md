# 59 小時執行計畫（Round 1）

起點 T0 = 2026-11-14 08:47（UTC+8），截止 T0+59h = 2026-11-16 19:47（UTC+8）。每 12 小時一個里程碑，最後 11 小時為收斂與提交。截止時間以大會公告為準，T0 若有出入，所有時間點平移即可。

| 里程碑 | 時間點 | 完成定義 |
|---|---|---|
| M0 | T0+0h | 本 repo 骨架：harness、資料格式、計畫、kit 清單 |
| M1 | T0+12h（11/14 20:47） | 真實模型 baseline 軌跡 |
| M2 | T0+24h（11/15 08:47） | 正式 zh-Hant 成對資料集 v1 |
| M3 | T0+36h（11/15 20:47） | 介入實驗完成 |
| M4 | T0+48h（11/16 08:47） | 結果凍結、AuditKIT 全面複核 |
| M5 | T0+59h（11/16 19:47） | 提交 |

## M0 → M1（0 到 12 小時）：baseline 跑起來

- 建 Python 3.12 環境安裝 AgentTune（原始碼，固定 commit `1dfd054`），確認 `Project.collect()` 能對 `data/samples/tools.json` 的四個工具產生 EventLog。
- 實作 mock 工具伺服器（決定性輸出，回傳值寫死在 repo），讓工具時間可重現。
- 選 2 個模型（候選：Tiny Aya、Command R7B，CircuitKIT 與 SafeTune 皆已驗證過），各跑 8 題示範任務 × 3 seeds。
- 驗收：`make demo` 對真實軌跡產生報告，`make xcheck` 全部一致。

## M1 → M2（12 到 24 小時）：資料集 v1

- 以 CuratorKIT 從台灣情境資料（時刻表、匯率公告）生成任務草稿，人工審核後擴充到 60 對（120 題），每個語言現象標籤至少 8 對。
- 加入「不應呼叫工具」與「需要兩步以上工具」兩類題目，避免 tool-call F1 被單步題主導。
- 繁簡檢查改接 OpenCC，所有 zh-Hant 指令與參考答案零簡體字。
- 驗收：`validate_all` 零錯誤，資料集與 checksum 進 repo。

## M2 → M3（24 到 36 小時）：主實驗與介入

- Baseline：2 模型 × 120 題 × 3 seeds。
- 介入 A（執行邊界正規化）：工具執行前套用 `zh_norm` 與 12 小時制轉換。
- 介入 B（prompt 層）：system prompt 明示台灣格式規則。
- 每組都記錄延遲，確認介入增加的延遲在預算內。
- 驗收：每組 `run_id` 的報告產生完畢，`exact` 與 `zh_norm` 差距、成功率、p95 延遲三張主表可讀。

## M3 → M4（36 到 48 小時）：分析與複核

- 依 `pair_id` 做 zh-Hant 與 en 的配對比較，bootstrap 信賴區間（可用 AuditKIT 的顯著性工具）。
- 依語言現象標籤拆解落差來源，驗證或推翻 README 的 claim，結果誠實改寫 claim。
- 錯誤分析：抽 20 個失敗 episode 人工標註失敗類別。
- 選做（只有在進度超前時）：AlignTune GRPO 搭配 `json_schema_verifiable` 獎勵小規模微調，SafeTune `evaluate()` 量測安全漂移。
- 驗收：結果凍結，之後只修文件不重跑實驗。

## M4 → M5（48 到 59 小時）：提交

- 撰寫結果章節與限制（樣本數、mock 工具、模型數量）。
- 乾淨環境重現：新 clone、照 README 指令跑一次，報告 checksum 與 repo 內版本一致。
- 文字檢查：台灣用詞、無破折號、無簡體字。
- T0+56h 停止新增內容，剩餘 3 小時只做提交與緩衝。

## 風險與備案

| 風險 | 備案 |
|---|---|
| AgentTune 在可用硬體上跑不起來 | 直接以模型 API 產生軌跡並寫成 `eiraku.traj/0.1`，評測不受影響 |
| 模型取得受限（gated） | 改用公開權重的同級模型，`model.revision` 固定 |
| 120 題標註來不及 | 降到 40 對，優先保留每個標籤的覆蓋 |
| 時間落後超過 6 小時 | 砍掉所有選做工作與介入 B |
