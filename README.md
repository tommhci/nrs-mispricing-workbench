# NRS-1 — Narrative-Reality Mispricing Workbench

AI agent that detects mismatches between market narratives and engineering reality.

## Live Dashboard
Deploy to: [share.streamlit.io](https://share.streamlit.io)

## Run locally
```bash
pip install streamlit pandas plotly requests anthropic yfinance
streamlit run app.py
```

## Run pipeline
```bash
python nrs1_v2.py --stub    # no API key needed
python nrs1_v2.py           # live mode (set ANTHROPIC_API_KEY)
```

*Not investment advice.*
