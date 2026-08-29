# NRS-1 — Narrative-Reality Mispricing Workbench

NRS-1 is a small autonomous agent that stress-tests a public
AI/semiconductor narrative against available engineering evidence and
market context. It runs a perception → reasoning → action pipeline
through an LLM (GLM/Zhipu), passes outputs through validation gates,
computes a "Gap Index" between narrative and reality, and emits an
append-only audit trail, run history, a structured report, and a
Streamlit dashboard.

Built as an academic prototype (HCI course project, now frozen).
**It is not investment advice; its scores are experimental and
uncalibrated.** What makes it worth reading is not the scoring — it is
the discipline around it: every run is reproducible, logged, auditable,
and wired into a governance control plane (protected paths, pre-commit
enforcement, attach-time verification).

## Highlights

- **Append-only audit trail** (`nrs1_audit.jsonl`) — every agent decision
  is recorded; nothing is silently overwritten.
- **Run history + SQLite mirror** (`nrs1_history.jsonl` → `db.py`) —
  dashboards and backtests query the same canonical log.
- **CI workflows** (`.github/workflows/`) — scheduled pipeline and
  historical backtest validation.
- **Deterministic fallbacks** — stub mode runs the full pipeline with
  zero API keys; missing yfinance/API keys degrade explicitly, never
  silently.
- **Governance surface** — the repo carries an
  [ai-control-plane](https://github.com/tommhci/ai-control-plane)
  attach: protected paths, ownership docs, and attach-time verification
  (see `AGENTS.md`, `.control-plane/`).

## Quick Start

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

python nrs1_v3.py --stub
python nrs1_v3.py --test
streamlit run app.py
```

> ⚠ `--stub` **overwrites** `nrs1_report.md` and appends to the audit /
> history logs. The committed `nrs1_report.md` is a frozen submission
> artifact — do not run pipeline modes against it casually.

Live GLM/Zhipu mode requires an API key:

```powershell
.\venv\Scripts\Activate.ps1
$env:ZHIPU_API_KEY="<your Zhipu API key>"
$env:GLM_MODEL="glm-4.5"
python nrs1_v3.py --test-llm
python nrs1_v3.py
```

## Commands

| Command | Purpose |
|---|---|
| `python nrs1_v3.py --stub` | Full pipeline with sample data, no API key (**overwrites report + appends logs**) |
| `python nrs1_v3.py` | Live mode with GLM and real source retrieval |
| `python nrs1_v3.py --test` | Built-in unit tests |
| `python nrs1_v3.py --test-llm` | Verify GLM API connectivity |
| `python nrs1_v3.py --backtest` | Historical formula validation |
| `streamlit run app.py` | Open the dashboard |

## Architecture

```text
Sources -> GLM LLM agents -> validation gates -> Gap Index -> report/history/dashboard
```

- `nrs1_v3.py`: main pipeline, GLM calls, validation gates, scoring,
  built-in tests.
- `app.py`: Streamlit dashboard for the latest run and history.
- `db.py`: SQLite query layer populated from `nrs1_history.jsonl`.
- `nrs1_history.jsonl` / `nrs1_audit.jsonl`: append-only run history and
  decision audit.
- `nrs1_report.md`: latest generated report (currently a frozen
  submission artifact).
- `NRS1_v3_SPEC.md`: pipeline design specification.
