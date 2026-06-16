# NRS-1 — Narrative-Reality Mispricing Workbench

An automated agent that scores the gap between what market narratives **claim**,
what engineering/physics **allows**, and what the market has **priced in** —
the **Gap Index**. Built to detect when a story about AI/semiconductors runs
ahead of (or behind) engineering reality.

> **Not investment advice. All scores are experimental and uncalibrated.**

## Quick start

```bash
pip install openai requests yfinance pandas streamlit plotly
export ZHIPU_API_KEY=<key from open.bigmodel.cn>    # for live mode

python nrs1_v3.py --stub          # sample data, no API key needed
python nrs1_v3.py                 # live mode (GLM + tiered sources)
streamlit run app.py              # dashboard
```

Full setup instructions: see **[SETUP.md](SETUP.md)**.

## Commands

| Command | Purpose |
|---------|---------|
| `python nrs1_v3.py --stub` | Run pipeline with hardcoded sample data |
| `python nrs1_v3.py` | Live mode (needs `ZHIPU_API_KEY`) |
| `python nrs1_v3.py --test` | Run unit tests (41) |
| `python nrs1_v3.py --backtest` | Formula validation on 5 historical events |
| `python nrs1_v3.py --backtest-live` | Test RealityAgent on real EDGAR docs |
| `python nrs1_v3.py --analyze` | Formula sensitivity analysis |
| `python nrs1_v3.py --help` | Full command reference |
| `python research_agent.py NVDA "topic"` | AI web-search research agent |
| `streamlit run app.py` | Dashboard |

## Architecture

```
Sources (tiered)    →  LLM Agents (GLM)   →  Gap Index    →  Output
  Tier 1: SEC EDGAR     NarrativeAgent        N vs R vs M     report.md
  Tier 2: Expert RSS    RealityAgent          5-gate check    history.jsonl
  Tier 3: News RSS      (3-run aggregation)                   SQLite + dashboard
```

- **LLM:** GLM (Zhipu AI / Z.ai) via OpenAI-compatible API
- **Storage:** JSONL (source of truth) + SQLite (query layer, auto-migrated)
- **Schedule:** GitHub Actions, twice on weekdays (10am & 6pm ET)

See **[NRS1_v3_SPEC.md](NRS1_v3_SPEC.md)** for the full technical specification.

*Not investment advice.*
