# Lexsi 六個 kit 的 T3 功能清單

讀取日期 2026-11-14，來源為 GitHub org [`Lexsi-Labs`](https://github.com/Lexsi-Labs)，下表連結全部固定在讀取時的 commit，方便重現。

| kit | commit | 對 T3 的角色 |
|---|---|---|
| [AgentTune](https://github.com/Lexsi-Labs/AgentTune/tree/1dfd054ce59e1c1bdd566cb7e5826f836b1c8c2e) | `1dfd054` | 產生與訓練 agent 軌跡（主要） |
| [AuditKIT](https://github.com/Lexsi-Labs/AuditKIT/tree/556eb4852d515391294203232ca3c90bdd48251d) | `556eb48` | 評測 agent 軌跡（主要） |
| [CuratorKIT](https://github.com/Lexsi-Labs/CuratorKIT/tree/ed45ae3489bfb6ee12c969e5d07e0309b758e829) | `ed45ae3` | 產生 zh-Hant 任務與訓練資料 |
| [AlignTune](https://github.com/Lexsi-Labs/aligntune/tree/b6e924e00d2b299f0eebb87429bd82ef2eccc896) | `b6e924e` | 以可驗證獎勵訓練工具呼叫 |
| [SafeTune](https://github.com/Lexsi-Labs/SafeTune/tree/cc3de5fd8f56845e74d6e88a0973c11cbaebdf5f) | `cc3de5f` | 微調後的安全性量測與修復 |
| [CircuitKIT](https://github.com/Lexsi-Labs/CircuitKIT/tree/6e9782bbf47f85e02a12853cb412dc09426b61d5) | `6e9782b` | 壓縮模型以換取延遲，可解釋性分析 |

**關鍵發現**：AuditKIT 的 [`tests/test_track3_agenttune.py`](https://github.com/Lexsi-Labs/AuditKIT/blob/556eb4852d515391294203232ca3c90bdd48251d/tests/test_track3_agenttune.py) 把 Track 3 的官方路線寫成「AgentTune 產生軌跡，AuditKIT 評分」，涵蓋 AgentTune trajectory JSONL、EventLog JSONL、`lexsi_provenance.json`，以及 OpenAI（`name`／`arguments`）與 Cohere（`tool_name`／`parameters`）兩種工具呼叫格式。本專案的資料格式與 adapter 依此設計。

## AgentTune：軌跡產生與 agentic 訓練

- **統一軌跡格式 `EventLog`**（[`agentic/events.py`](https://github.com/Lexsi-Labs/AgentTune/blob/1dfd054ce59e1c1bdd566cb7e5826f836b1c8c2e/src/agenttune/agentic/events.py)）：事件種類 `TEXT`、`REASONING`、`TOOL_CALL`、`TOOL_RESULT`、`OBSERVATION`、`TURN_COMPLETE`、`REWARD`、`MEMORY_OP`，分 `light` 與 `full` 兩個 tier。`to_eval_dict()` 只投影 `tool_calls` 與 `tool_outputs`，所以延遲與最終狀態要由我們自己的格式保留。
- **`Project` 管線**（[`agentic/project.py`](https://github.com/Lexsi-Labs/AgentTune/blob/1dfd054ce59e1c1bdd566cb7e5826f836b1c8c2e/src/agenttune/agentic/project.py)）：`collect_rollout()`、`collect()`、`evaluate_agentic()`、`heal()`（self-healing）、`distill()`。
- **`TrajectoryEvaluator`**（[`eval/agentic/trajectory_eval.py`](https://github.com/Lexsi-Labs/AgentTune/blob/1dfd054ce59e1c1bdd566cb7e5826f836b1c8c2e/src/agenttune/eval/agentic/trajectory_eval.py)）：程式化指標 TAC（Tool Argument Correctness）、TER（Tool Efficacy Reward）、ARR（API Redundancy Ratio）、SCSR（Self-Correction Success Rate）、RAD（Reasoning-to-Action Density）、LCF（Loop Collapse Frequency），另有 `_calculate_latency()`。
- **訓練**：agentic rollout 搭配 GRPO、PPO、DPO、RLOO、BCO，內建獎勵含 `finqa`、`sql`、`hybrid_prm`、`distilled_judge` 等。
- **限制**：`requires-python >=3.12`，且目前不在 PyPI，需從原始碼安裝。本專案核心 harness 因此不依賴 AgentTune，只透過 adapter 讀寫 EventLog。

## AuditKIT：T3 評測

- **工具呼叫指標**（[`metrics/agent.py`](https://github.com/Lexsi-Labs/AuditKIT/blob/556eb4852d515391294203232ca3c90bdd48251d/src/auditkit/metrics/agent.py)）：`ToolCallF1`（多重集合 precision／recall／F1／exact）、`TrajectoryMatch`、`ParallelToolCalls`、`ToolCallValidity`、`RedundantToolCalls`、`AgentLoopDetection`、`ToolPermission`，LLM 評審類 `TaskCompletion`、`ToolSelectionJudge`。
- **延遲**（[`metrics/perf.py`](https://github.com/Lexsi-Labs/AuditKIT/blob/556eb4852d515391294203232ca3c90bdd48251d/src/auditkit/metrics/perf.py)）：`LatencyStats`（mean、min、max、p50、p95、p99、std）、`Throughput`。
- **episode 評測**（[`agent_eval/`](https://github.com/Lexsi-Labs/AuditKIT/tree/556eb4852d515391294203232ca3c90bdd48251d/src/auditkit/agent_eval)）：`importers.py` 的 `episode_from_eventlog()` 直接匯入 AgentTune EventLog，`outcome.py` 判定完整 episode 結果；支援離線（`precomputed`）評測。
- **可用性**：核心為 stdlib-only，PyPI `auditkit==1.1.11` 可在 Python 3.10 安裝。本專案 `make xcheck` 用它逐題核對 F1 與 exact。

## CuratorKIT：zh-Hant 任務與訓練資料

- 八種生成任務：QA、preference pairs、**GRPO rollouts**、**multi-turn**、Evol-Instruct、chain-of-thought、adversarial variants，可接任何 LiteLLM 相容 API 或本機 Ollama／vLLM。
- 品質 gate：grounded hallucination gate（對照原始段落驗證）、reward 與 diversity gate，失敗樣本會診斷並修復後重新過 gate。
- 匯出：Alpaca、ShareGPT、chat messages、DPO、GRPO、PPO，可直接給 TRL 與 AlignTune。
- **可重現性**：每次執行輸出 `manifest.json`、`rejected.jsonl`、`dataset_card.md`、`lexsi_provenance.json` 與 SHA-256 `checksums.txt`。`lexsi_provenance.json` 正是 AuditKIT T3 測試會讀的檔案。
- T3 用途：以台灣情境資料（台鐵時刻、匯率、行事曆）生成 zh-Hant 成對任務與 adversarial 格式變體。

## AlignTune：工具呼叫的可驗證獎勵訓練

- 多後端（TRL、Unsloth）SFT 與 RLHF，含 GRPO 等 RL 演算法。
- 獎勵登錄表（[`rewards/registry.py`](https://github.com/Lexsi-Labs/aligntune/blob/b6e924e00d2b299f0eebb87429bd82ef2eccc896/src/aligntune/rewards/registry.py)）含 `json_schema_verifiable`（`RewardType.JSON_SCHEMA_VERIFIABLE`），可把本專案的 Schema 合法率直接當成獎勵。
- T3 用途：若 Round 1 後有時間，以 GRPO 搭配 Schema 獎勵與 `zh_norm` 工具呼叫 F1 做小規模微調，作為「訓練面介入」與「執行邊界正規化」的對照。

## SafeTune：自主性提升後的安全檢查

- 主要入口：`SafeGradTrainer`（harden）、`ReStaTrainer`（recover，免訓練）、`RefusalDirectionTrainer`（steer）、`RMUTrainer`／`NPOTrainer`（unlearn）、`identify_safety_neurons`（interpret）、`safetune.evaluate.evaluate()`（measure）。
- README 有 Cohere／hackathon notes，例如 Tiny Aya chat template 會加約 366 token 的 system preamble。
- T3 用途：若做了工具呼叫微調，用 `evaluate()` 量測安全漂移，確認提升自主性沒有犧牲拒答行為。非主線。

## CircuitKIT：延遲與可解釋性

- Discover（EAP、EAP-IG、EAP-GP、ACDC、IBCircuit、CD-T）、Evaluate、Prune、Quantize（3／4-bit）、Edit（ROME／MEMIT）、Steer、circuit-restricted LoRA、lm-evaluation-harness Benchmark。
- 支援 Tiny Aya、Command R7B、Aya Expanse 8B 等 Cohere 家族模型。
- T3 用途：剪枝或量化可降低模型時間（本專案的 `model_ms`），可量測「延遲下降」與「工具呼叫 F1」的取捨。非主線。

## 本專案對應

| 需求 | 用到的 kit | 本 repo 位置 |
|---|---|---|
| 軌跡產生 | AgentTune `Project.collect()` | `evals/adapters/agenttune.py` |
| 任務成功率 | AuditKIT `agent_eval.outcome`（概念），自製程式化 oracle | `evals/metrics.py` `task_outcome` |
| 工具呼叫正確率 | AuditKIT `ToolCallF1`（交叉驗證） | `evals/metrics.py` `tool_call_accuracy`、`evals/adapters/auditkit_xcheck.py` |
| 延遲 | AuditKIT `LatencyStats`（同樣的 p50／p95 定義） | `evals/metrics.py` `latency_summary` |
| zh-Hant 任務擴充 | CuratorKIT | `data/`（待擴充） |
| 訓練面介入（選做） | AlignTune、SafeTune、CircuitKIT | 待定 |
