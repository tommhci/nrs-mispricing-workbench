# NRS-1 Submission Specification

## Purpose

NRS-1 is an academic AI-agent prototype for comparing public AI hardware narratives against engineering evidence and market context. It is designed for human review, not autonomous trading.

## Provider

The final submission provider is GLM/Zhipu AI. The code uses GLM through the OpenAI-compatible chat completions interface:

```python
OpenAI(api_key=ZHIPU_API_KEY, base_url="https://open.bigmodel.cn/api/paas/v4/")
```

Runtime environment variables:

```powershell
$env:ZHIPU_API_KEY="<your Zhipu API key>"
$env:GLM_MODEL="glm-4.5"
```

`GLM_MODEL` is optional.

## Agent Flow

```text
Perception -> Reasoning -> Action
```

- Perception: retrieve candidate AI/semiconductor source documents.
- Reasoning: use two GLM-backed LLM calls for narrative extraction and engineering-reality assessment.
- Action: validate fields, compute the Gap Index, write report/history outputs, and show the result in Streamlit.

## Deterministic Boundary

The LLM interprets and structures language. Python handles validation, arithmetic, gates, report writing, history logging, and dashboard data loading.

This boundary is important for the worksheet because it makes the agent inspectable and testable.

## Main Files

| File | Role |
|---|---|
| `nrs1_v3.py` | Main pipeline, GLM API call, scoring, gates, built-in tests |
| `app.py` | Streamlit dashboard |
| `db.py` | SQLite migration/query layer |
| `nrs1_history.jsonl` | Append-only run history |
| `nrs1_report.md` | Latest generated report |

## Verification Commands

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python nrs1_v3.py --stub
$env:ZHIPU_API_KEY="<your Zhipu API key>"
python nrs1_v3.py --test-llm
python nrs1_v3.py
python nrs1_v3.py --test
streamlit run app.py
```

## Limitations

- RSS/source retrieval can return weakly relevant items.
- Scores are design weights, not calibrated predictions.
- The system should report insufficient evidence rather than forcing a number.
- The dashboard is a review surface, not an execution or trading interface.

Not investment advice. Academic prototype only.
