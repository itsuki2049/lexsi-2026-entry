# HF Dataset Card（發佈備援草稿）

用途：Sensei HF token 到位後，以此卡建立 HuggingFace dataset repo（建議名 `eiraku/zh-hant-agent-trajectories`）＋ collection，一行即可發佈。

---

---
language:
- zh
license: apache-2.0
task_categories:
- tool-calling
- agentic-trajectory-evaluation
tags:
- agentic
- tool-use
- traditional-chinese
- taiwan
- synthetic
- safety-gate
size_categories:
- n<1K
configs:
- config_name: default
  data_files: trajectories/*.json
---

# eiraku/zh-hant-agent-trajectories (v0.1.0)

Replayable Traditional Chinese (Taiwan) agentic tool-calling trajectories with programmatic safety labels, built for the Lexsi AI Safety & Alignment Hackathon 2026 (Track 3).

## Description
22 zh-Hant-TW trajectories replayed in a deterministic in-memory stub environment from 22 Taiwanese-scenario seed tasks (web verification, file operations, API integration, data cleaning, scheduling). Every trajectory records tool events with timestamps, permissions, expected/final state, and per-label evidence pointers. Byte-reproducible: same seed file yields identical trajectories (`generate_trajectories.py --check`).

## Splits
train 12 / dev 7 / test 3, split by template hash to prevent leakage.

## Risk coverage
none 13 · misinformation 3 · destructive_action 2 · prompt_injection 1 · prompt_injection_near_miss 1 · external_contact 1 · pii_handling 1

## Quality gates
- Official v0.1 validator: **PASS (errors=0, warnings=0)** (schema, secret/PII regex, safety-label consistency, exact duplicates, split leakage, replay, manifest)
- Manual review: five items completed (locale naturalness, license validity, oracle correctness, tool safety, prompt-injection labels); high-severity injection cases dual-reviewed; record in `evals/manual-review-record.md`
- Not implemented: `duplicate_semantic` (semantic near-duplicate)

## Safety
Stub environment is in-memory, offline (reserved domains only, /sandbox paths only). Provenance per file in `data/manifests/trajectories-zh-Hant.json`; `generation_model: null` (rule-based generation, no LLM).

## License
Apache-2.0. Rights holder: EIRAKU (itsuki2049). Authors are team AI seats (disclosed in the record).

## Limitations
Synthetic data, not human-annotated model outputs; no real-model trajectories in v0.1; semantic near-duplicate check pending.

## Citation
EIRAKU (2026). zh-hant-agent-trajectories v0.1.0. Lexsi AI Safety & Alignment Hackathon 2026.
