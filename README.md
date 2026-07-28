# NRS-1 - Narrative-Reality Mispricing Workbench

NRS-1 is a small AI agent for the HCI Worksheet 07 submission. It compares a public AI/semiconductor narrative with available engineering evidence and market context, then writes a report, history log, and Streamlit dashboard view.

This is an academic prototype. It is not investment advice, and its scores are experimental and uncalibrated.

## Quick Start

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

python nrs1_v3.py --stub
python nrs1_v3.py --test
streamlit run app.py
```

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
| `python nrs1_v3.py --stub` | Run the full pipeline with sample data and no API key |
| `python nrs1_v3.py` | Run live mode with GLM and real source retrieval |
| `python nrs1_v3.py --test` | Run built-in unit tests |
| `python nrs1_v3.py --test-llm` | Verify GLM API connectivity |
| `python nrs1_v3.py --backtest` | Run historical formula validation |
| `streamlit run app.py` | Open the dashboard |

## Architecture

```text
Sources -> GLM LLM agents -> validation gates -> Gap Index -> report/history/dashboard
```

- `nrs1_v3.py`: main pipeline, GLM calls, validation gates, scoring, built-in tests.
- `app.py`: Streamlit dashboard for the latest run and history.
- `db.py`: SQLite query layer populated from `nrs1_history.jsonl`.
- `nrs1_history.jsonl`: append-only run history.
- `nrs1_report.md`: latest generated report.
- `NRS1_v3_SPEC.md`: concise submission specification.

## Submission Evidence

Prepare four screenshots for the worksheet:

1. Repository structure: proves the project has the expected runnable files.
2. Agent running in terminal: proves the perception-reasoning-action pipeline executes.
3. Streamlit dashboard: proves the result is visible in a human-review interface.
4. Tests passing: proves deterministic scoring and failure-handling checks pass.
