# zh-Hant Agent Trajectories: Replayable Traditional Chinese Tool-Calling Dataset and Evaluation Framework

**One-line claim**: The gap when agents handle Traditional Chinese (Taiwan) tool-calling tasks comes mainly from surface formatting (臺/台 variants, Minguo dates, thousands separators, 12-hour clock) rather than task comprehension; a zh-Hant normalization layer at the execution boundary recovers most of the gap within latency budget.

> **Status**: Stage 1 submission. The claim above is a hypothesis with the full pipeline (dataset, harness, judge rubric, safety gate) built and validated; real-model baseline/intervention experiments are planned as the immediate next step. All numbers are labeled with sources.

**Team**: EIRAKU. Repo: [itsuki2049/lexsi-2026-entry](https://github.com/itsuki2049/lexsi-2026-entry) (Apache-2.0).

## Tracks
- **Track 3: Agentic Fine-tuning** — reliable tool calling, trajectory quality, safety boundaries, reward-hacking prevention.

## Abstract
Taiwanese users type 臺北市 not 台北市, Minguo years not ISO dates, 下午3點 not 15:00 — and agent evaluations almost never measure whether failures come from these surface mismatches or from real task misunderstanding. We built: (1) a replayable zh-Hant trajectory dataset: 22 Taiwanese-scenario seed tasks replayed into byte-reproducible trajectories with a stub tool environment (9 risk-relevant cases); (2) a three-metric harness (task success / tool-call accuracy / latency) whose tool-call F1 compares `exact` vs `zh_norm` modes — the delta isolates surface-format loss — cross-checked per-episode against AuditKIT `ToolCallF1`; (3) a judge rubric and data-quality safety gate (unauthorized actions = hard fail).

## Hackathon Repos Used
- **AuditKIT** (PyPI 1.1.11, commit `556eb48`): executed — `make xcheck` confirms our exact-match F1 matches AuditKIT ToolCallF1 on all 8 demo episodes.
- **AgentTune** (commit `1dfd054`): adapter implemented — bidirectional conversion between our trajectories and EventLog (light tier).
- **CuratorKIT** (commit `ed45ae3`): planned — zh-Hant task generation for Stage 2 (Stage 1 seeds authored by our team).

## How the Repos Connect
CuratorKIT-style seeded tasks → our replay generator + stub env → v0.1 ingestion validator (errors=0) → AgentTune EventLog adapter → eval harness → AuditKIT cross-check. Official Track 3 path (AgentTune produces, AuditKIT scores) is honored by design.

## Models and Licenses
No model evaluated in Stage 1 (candidates: Tiny Aya, Command R7B; licenses to be confirmed before use). No Lexsi kit source redistributed. Dataset Apache-2.0, synthetic, rights holder EIRAKU (itsuki2049).

## Datasets
`data/trajectories/zh-Hant/`: 22 zh-Hant-TW trajectories, 72 tool events; categories: web verification 5, file ops 4, API integration 5, data cleaning 4, scheduling 4; split train 12 / dev 7 / test 3 (template-hash). All URLs reserved domains, paths under /sandbox, placeholder names only. Quality: validator PASS (errors=0, warnings=0); five manual-review items completed including two-reviewer sign-off on high-severity prompt-injection cases; `duplicate_semantic` NOT RUN (listed in limitations). Security hardening: five rounds of adversarial review found and fixed four sandbox-bypass classes (substring approval matching, network-allowlist non-enforcement, fs_root preservation, dot-segment traversal, cross-tool pre-approval reuse), each fixed with a dedicated negative fixture.

## Metrics
Task success (final-state subset match, fabrication scores 0) · tool-call F1 (exact / zh_norm / name modes) · latency (mean/p50/p95/max) · safety gate (S01-S05, pass/fail) · simplified-character leakage · composite = 100 × (0.50·success + 0.30·accuracy + 0.20·latency) ÷ 4, safety fail = 0, anti-reward-hacking: latency zeroed if success or accuracy < 2.

## Results (Before vs After)
Headline (handwritten demo only, NOT model output): zh-Hant tool-call F1 0.167 (`exact`) → 0.917 (`zh_norm`), n=4 — demonstrates the metric isolates surface-format loss. Real-model baseline and normalization-intervention experiments: **NOT RUN in Stage 1** (protocol defined in repo; paired bootstrap by `pair_id` planned).

## Limitations
No real-model results yet. `duplicate_semantic` check not implemented. Reviewers are team AI seats (disclosed), not independent humans. Stub environment is in-memory only.

## Artifacts
Dataset + validator + harness + judge rubric + safety gate + six-round manual-review record: all in the public repo. `make demo`, `make xcheck`, `python -m pytest tests/` reproduce (43 tests passing).

## LLM Usage Statement
Code, seed tasks, and docs written by Devin (Cognition) under EIRAKU team direction; review and QA by team seats luna (GPT-6) and yuuka (GLM); no LLM generated trajectory content (generator is rule-based, provenance `generation_model: null`).

---

*Lean draft (fallback), condensed from `submission/write-up.md` at main `34ca5c5`. All claims sourced in the write-up.*
