# zh-Hant 資料集品質與安全 Gate

版本：0.1 草案，20261114  
用途：Lexsi Track 3 agent trajectory、工具呼叫紀錄、judge label 及合成資料的入庫檢查。

## 入庫前阻擋項

以下任何一項失敗即 `BLOCKED`，不得訓練、公開或作為安全結論依據。

- **來源與授權**：每個樣本標 source、作者或生成流程、license、取得日期與修改紀錄。授權不明或禁止再散布的內容不入庫。合成樣本標記合成並記錄生成模型與版本。
- **個資與秘密**：掃描 API key、token、密碼、cookie、電子郵件、電話、地址、真實姓名與識別碼；使用 regex、secret scanner 加人工抽查。任何疑似真實 secret 或未授權個資先隔離，確認並刪除或取得合法授權前不公開。測試憑證使用明確無效的 placeholder 和保留網域。
- **危險指令**：安全測試可保留必要的威脅意圖、攻擊類別與脫敏片段，但不可放入可直接執行的高風險操作步驟、真實目標、有效 payload 或憑證。工具輸出中的 prompt injection 樣本應標成不可信資料，以 inert fixture 呈現，不能在匯入或測試時執行。
- **副作用隔離**：所有 agent 工具指向 mock 或 disposable sandbox。拒絕 production URL、真實付款、刪除、發信、公開發文及外部帳號操作。每條軌跡都有 allowlist、權限說明、可回復策略及 audit trace。
- **標籤安全一致性**：至少有安全風險類別、嚴重度、允許或禁止行為、預期 safe alternative、是否需人工確認。不能把「拒絕一切」標成通用安全 gold，也不能把越權但完成任務的軌跡標成成功。

## zh-Hant 語言品質

- 每筆指定 `zh-Hant-TW` 或 `zh-Hant-HK` 等明確 locale，不以泛 `zh` 混淆繁簡或區域詞彙。
- 母語標註者檢查繁體字形、用詞、語序、標點、專有名詞及自然度。跨 locale 版本分開保留，避免把台灣用語當作香港唯一正解，反之亦然。
- 檢查中英混排、程式碼區塊、JSON、工具名稱、引號和標點不被轉換器破壞；工具 schema 與英文識別符原樣保留。
- 不以字形轉換當語言本地化；逐筆檢查語義是否保留，特別檢查否定詞、權限、數量、日期、條件與安全邊界。

## 任務與軌跡品質

- 任務目標、初始狀態、工具 schema、權限、時間預算及 expected state 欄位完整且互相一致。
- 自動驗證工具名稱、參數型別、必要欄位、返回 schema、時間戳、event 順序、case id 唯一性及 JSON 可解析性。
- 可重播軌跡須以乾淨環境重播。驗證 final state，而非只比對文字。工具結果錯誤或環境故障要另標，不當成 agent 違規。
- Oracle 接受多個安全且有效的工具策略，除非任務明確要求特定操作順序。所有被拒絕策略要記錄原因，避免用單一示範軌跡誤判合法差異。
- 去重採 exact hash 加語義近重複檢查。train/dev/test 按 template、任務來源、目標與工具環境分組切分，避免同一模板改名後跨 split 洩漏。holdout red-team seeds 不參與生成、prompt 開發或 judge 校準。
- 每個安全類別都有 benign near-miss 對照，監測 over-refusal；每類保留安全替代方案和可衡量的任務完成條件。

## 人工覆核與發佈閘

- 所有高嚴重度安全案例、所有 LLM judge 不確定案例、所有涉及資料外洩或不可逆工具的案例全數雙人覆核。
- 一般抽樣依風險、locale、工具、難度與標籤分層。保存標註者 id 的匿名代碼、時間、原始 label、分歧和裁決記錄。
- 逐項跑自動 schema、secret、PII、duplicate、split leakage 和 replay 檢查。每次資料修改都重跑受影響的 checks，並更新 dataset hash。
- 公開 HF 前以另一位未參與製作的人做安全抽查，檢視實際下載檔案、README、metadata、examples、logs 和附件，確認沒有私密資料或未脫敏的危險內容。
- Gate 結果只可為 `PASS`、`BLOCKED` 或 `PASS WITH NOTES`。`PASS WITH NOTES` 不可用於存在 secret、PII、授權不明、可執行危險操作或安全標籤失真的資料。

## 每次審查的輸出

記錄 dataset name、version、commit 或 revision、檔案 hash、樣本數與 locale 分布、各風險類別數、license/source 檢查、secret/PII 掃描工具版本與結果、重播通過率、近重複率、split leakage 結果、人工覆核 sample、agreement、未解分歧、裁決者及最終 gate。任何未執行項目標 `NOT RUN`，不可寫成通過。
