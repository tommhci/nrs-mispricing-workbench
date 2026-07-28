# NRS-1 Setup Guide

This setup is intentionally minimal for the Worksheet 07 submission.

## 1. Install

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 2. Run Without An API Key

```powershell
python nrs1_v3.py --stub
```

Stub mode uses sample data. It is the fastest way to prove the pipeline, report writer, history writer, and dashboard data path are runnable.

## 3. Configure GLM/Zhipu Live Mode

Create a Zhipu API key, then set it in the terminal:

```powershell
$env:ZHIPU_API_KEY="<your Zhipu API key>"
$env:GLM_MODEL="glm-4.5"
```

`GLM_MODEL` is optional. If omitted, the code defaults to `glm-4.5`.

## 4. Verify GLM Connectivity

```powershell
python nrs1_v3.py --test-llm
```

Expected result: a parsed JSON object similar to:

```json
{"status": "ok", "model": "glm"}
```

## 5. Run Live Mode

```powershell
python nrs1_v3.py
```

Live mode retrieves sources, calls the two GLM-based LLM agents, validates output, computes the Gap Index, and writes `nrs1_report.md` plus `nrs1_history.jsonl`.

## 6. Run Tests

```powershell
python nrs1_v3.py --test
```

The worksheet summarizes six tested behaviors: normal scoring, insufficient-evidence handling, no-market-data fallback, invalid-weight rejection, full stub pipeline execution, and fixture verification. The built-in script may print additional lower-level checks for those behaviors.

## 7. Open Dashboard

```powershell
streamlit run app.py
```

Open the local URL printed by Streamlit, usually `http://localhost:8501`.

## Files

| File | Purpose |
|---|---|
| `nrs1_v3.py` | Main agent pipeline and built-in tests |
| `app.py` | Streamlit dashboard |
| `db.py` | SQLite query layer |
| `nrs1_history.jsonl` | Run history |
| `nrs1_report.md` | Latest report |
| `NRS1_v3_SPEC.md` | Concise technical spec |

Not investment advice. Academic prototype only.
