# Project Status

Project: nrs-mispricing-workbench
Project ID: nrs-mispricing-workbench
AI Control Plane: C:\Users\1\ai-control-plane
Attach tier: minimal
Attached: 2026-07-28

## Current State

- Goal: NRS-1 — a small AI agent for the HCI Worksheet 07 submission that
  compares a public AI/semiconductor narrative against engineering evidence
  and market context, then writes a report (`nrs1_report.md`), history log
  (`nrs1_history.jsonl`), audit trail (`nrs1_audit.jsonl`), and a Streamlit
  dashboard view. Academic prototype; scores experimental and uncalibrated;
  not investment advice.
- Working: full pipeline (narrative → evidence → scoring → report/history/
  audit outputs) run to submission (`83b7535 submission: finalize NRS-1 GLM
  worksheet`, 2026-07-28); stub + test modes (`nrs1_v3.py --stub/--test`);
  two GitHub Actions workflows tracked (`.github/workflows/daily_pipeline.yml`,
  `backtest_live.yml`); SQLite store `nrs1_data.db` auto-populated at runtime
  (gitignored).
- Do not change without owner approval: everything in
  `.control-plane/protected-paths.json` (AGENTS.md, AI_BOOTSTRAP.md,
  PROJECT_STATUS.md, `.control-plane/protected-paths.json`), the scoring
  logic in `nrs1_v3.py`, and the submitted worksheet artifacts
  (`nrs1_report.md`, `Worksheet_07_NRS1_Final_Master_GLM.docx` — the docx
  is currently untracked, owner should decide whether to track it).

## Verification

- No tests/ directory exists (no unit test baseline yet — known gap).
- Smoke commands (from README Quick Start):
  `python nrs1_v3.py --stub` (offline stub run) and
  `python nrs1_v3.py --test`.
- Last verified: not run this session (no test suite to execute; smoke run
  left to owner — requires venv activation).

## AI Working Constraints

- Read AGENTS.md and AI_BOOTSTRAP.md at session start.
- Load shared ai-control-plane protocols only when the current task needs them.
- Keep project-specific state in this repo, not in the shared control plane.
- Never commit: `venv/`, `__pycache__/`, `nrs1_data.db`, `.env`,
  runtime session logs (`.agents/session_log.jsonl`,
  `.agents/effectiveness_log.jsonl`).
