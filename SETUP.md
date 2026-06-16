# NRS-1 Setup Guide

> **This is the canonical setup document.** Follow it top-to-bottom.  
> Estimated time: 30 minutes for full live mode.

---

## 0. What You Actually Have

```
Repo main branch:  NRS-1 v3 core (PRs #1 + #2 merged)
                   Pipeline runs in STUB mode → fake data
Open PRs waiting:  #3 validation tools + consistency fix
                   #4 dashboard redesign + SQLite
                   #5 research agent (AI web search)
Your free tokens:  GLM on open.bigmodel.cn — not yet connected
```

**The system is fully built but running on hardcoded fake data.**  
The single most important action: connect your GLM API key.

---

## STEP 1 — Get Your GLM API Key

**Verified source:** roocode.com (2026), apidog.com (2025)

1. Go to **https://open.bigmodel.cn/**
2. Click **注册 / Register** (top right)
3. Sign up with your email
4. Verify your email
5. After login, go to **个人中心** (Account) → **API密钥** (API Keys)
6. Click **新建 API Key** (Create new key)
7. Give it a name like `nrs1-pipeline`
8. **Copy the key immediately** — it is shown only once
9. Store it somewhere safe (e.g. password manager)

> Your free token quota (verified: ~20M tokens with developer package as of 2026,  
> source: ainvest.com Jun 2026) is enough for hundreds of daily pipeline runs.

**Optional upgrade (recommended):**  
Set `GLM_MODEL=glm-4.7-flash` — free tier, significantly better than glm-4.5  
Source: codersera.com Jan 2026 (GLM-4.7-Flash is free on Z.ai API)

---

## STEP 2 — Add Secrets to GitHub

**Verified source:** docs.github.com/en/actions/security-for-github-actions

1. Go to: `https://github.com/tommhci/nrs-mispricing-workbench`
2. Click **Settings** tab (top of repo, not account settings)
3. Left sidebar → **Secrets and variables** → **Actions**
4. Click **New repository secret** for each:

| Secret Name | Value | Required? |
|-------------|-------|-----------|
| `ZHIPU_API_KEY` | Your key from Step 1 | **REQUIRED** |
| `GLM_MODEL` | `glm-4.7-flash` | Recommended |
| `GMAIL_SENDER` | your@gmail.com | Optional (email reports) |
| `GMAIL_APP_PASSWORD` | Gmail app password | Optional |
| `GMAIL_RECIPIENT` | recipient@email.com | Optional |

> **Gmail app password** (if you want email reports):  
> Google Account → Security → 2-Step Verification → App passwords → create one for "Mail"

---

## STEP 3 — Merge Pending PRs

Merge in this order (to avoid conflicts):

```
PR #3 first  → https://github.com/tommhci/nrs-mispricing-workbench/pull/3
PR #4 second → https://github.com/tommhci/nrs-mispricing-workbench/pull/4
PR #5 last   → https://github.com/tommhci/nrs-mispricing-workbench/pull/5
```

If GitHub shows a merge conflict on any PR:
1. Click "Resolve conflicts"
2. Keep ALL content from BOTH files (these are additive changes)
3. Remove only the `<<<<<<`, `=======`, `>>>>>>>` markers
4. Mark as resolved and commit

---

## STEP 4 — Enable Live Mode

After merging PR #4, edit `.github/workflows/daily_pipeline.yml`:

```yaml
# Find this line:
run: python nrs1_v3.py --stub

# Change to:
run: python nrs1_v3.py
```

Commit and push. The next scheduled run will use real GLM + real EDGAR data.

---

## STEP 5 — Verify Everything Works

**5a. Test GLM connection (in your terminal):**
```bash
git clone https://github.com/tommhci/nrs-mispricing-workbench
cd nrs-mispricing-workbench
pip install openai requests yfinance pandas streamlit plotly
export ZHIPU_API_KEY=<your key>
python nrs1_v3.py --test-llm
```

Expected: `{"status": "ok", "model": "glm"}` or similar JSON

**5b. Run unit tests:**
```bash
python nrs1_v3.py --test
```
Expected: `Results: 41 passed, 0 failed` (after all PRs merged)

**5c. Test EDGAR fetcher:**
```bash
python nrs1_v3.py --test-edgar NVDA
```
Expected: List of NVDA filings with engineering content

**5d. The critical validation — RealityAgent accuracy:**
```bash
python nrs1_v3.py --backtest-live
```
This is the most important test. It tells you if GLM can produce calibrated  
scores on real historical documents. See the verdict scale:

| MAE feasibility_score | Verdict |
|----------------------|---------|
| ≤ 0.15 | ✅ Signal is meaningful for production use |
| 0.15 – 0.30 | ⚠️ Directional only, noisy |
| ≥ 0.30 | ❌ Scores unreliable — fix agent before adding features |

**5e. Run the research agent:**
```bash
export ZHIPU_API_KEY=<your key>
python research_agent.py NVDA "Blackwell GPU production update June 2026"
```

---

## STEP 6 — View the Dashboard

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`

Three tabs:
- **◈ Overview** — current signal status
- **◷ Timeline** — N/R divergence chart (amber zone = mismatch signal)
- **⊙ Analysis** — evidence quality + source tier distribution

---

## STEP 7 — Manual Run (anytime)

Trigger manually in GitHub:
1. Go to repo → **Actions** tab
2. Select **NRS-1 v3 Pipeline**
3. Click **Run workflow**

Or locally:
```bash
python nrs1_v3.py          # live mode (needs ZHIPU_API_KEY)
python nrs1_v3.py --stub   # test without API key
python nrs1_v3.py --backtest     # formula validation
python nrs1_v3.py --analyze      # sensitivity analysis
python nrs1_v3.py --backtest-live # agent accuracy test
python nrs1_v3.py --help         # full command reference
```

---

## Environment Variables Reference

```bash
# Required for live mode
export ZHIPU_API_KEY=<from open.bigmodel.cn>

# Optional: upgrade model (glm-4.7-flash is free and better)
export GLM_MODEL=glm-4.7-flash

# Optional: email reports
export GMAIL_SENDER=your@gmail.com
export GMAIL_APP_PASSWORD=<app password from Google>
export GMAIL_RECIPIENT=recipient@email.com
```

---

## Files Reference

| File | Purpose |
|------|---------|
| `nrs1_v3.py` | Main pipeline + all tools |
| `research_agent.py` | AI-driven research with web search |
| `app.py` | Streamlit dashboard |
| `db.py` | SQLite data layer |
| `nrs1_history.jsonl` | Raw data (append-only) |
| `nrs1_data.db` | SQLite DB (auto-generated, gitignored) |
| `nrs1_report.md` | Latest pipeline report |
| `nrs1_audit.jsonl` | Audit trail (append-only) |
| `NRS1_v3_SPEC.md` | Full technical specification |

---

*Not investment advice. All scores experimental and uncalibrated.*
