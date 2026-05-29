"""
NRS-1 Streamlit Dashboard
=========================
Real-time Narrative-Reality Mispricing Workbench

Run locally:
    pip install streamlit pandas plotly
    streamlit run app.py

Deploy to web (free, 60 seconds):
    1. Push this folder to GitHub
    2. Go to share.streamlit.io → connect repo → deploy
    3. Get a public URL like: https://nrs1-mispricing.streamlit.app
"""

import streamlit as st
import json
import os
import math
import datetime
import pandas as pd

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NRS-1 Mispricing Workbench",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS — dark terminal aesthetic ──────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600;700&family=IBM+Plex+Sans:wght@300;400;600&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
}
code, .mono {
    font-family: 'IBM Plex Mono', monospace;
}

/* Dark header band */
.nrs-header {
    background: linear-gradient(135deg, #0a0a0a 0%, #111827 50%, #0f172a 100%);
    border-bottom: 2px solid #22d3ee;
    padding: 2rem 2.5rem 1.5rem;
    margin: -1rem -1rem 1.5rem;
    border-radius: 0 0 8px 8px;
}
.nrs-title {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.8rem;
    font-weight: 700;
    color: #22d3ee;
    letter-spacing: -0.02em;
    margin: 0;
}
.nrs-subtitle {
    font-size: 0.85rem;
    color: #64748b;
    margin-top: 0.3rem;
    font-family: 'IBM Plex Mono', monospace;
    letter-spacing: 0.05em;
}

/* Metric cards */
.gap-card {
    background: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 1.2rem 1.5rem;
    position: relative;
    overflow: hidden;
}
.gap-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: var(--accent, #22d3ee);
}
.gap-label-pill {
    display: inline-block;
    padding: 0.25rem 0.75rem;
    border-radius: 999px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.08em;
}
.STRONG_MISMATCH  { background: #fee2e2; color: #991b1b; }
.MODERATE_MISMATCH{ background: #fff7ed; color: #92400e; }
.WEAK_MISMATCH    { background: #fefce8; color: #854d0e; }
.ALIGNED          { background: #dcfce7; color: #166534; }
.INSUFFICIENT_EVIDENCE { background: #f3f4f6; color: #6b7280; }

.score-row {
    display: flex;
    gap: 0.5rem;
    margin: 0.8rem 0;
    align-items: center;
}
.score-bar-bg {
    flex: 1;
    height: 8px;
    background: #1e293b;
    border-radius: 4px;
    overflow: hidden;
}
.score-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.6s ease;
}
.score-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    color: #64748b;
    width: 70px;
    flex-shrink: 0;
}
.score-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    color: #e2e8f0;
    width: 50px;
    text-align: right;
    flex-shrink: 0;
}

.claim-box {
    background: #0a0a0a;
    border-left: 3px solid #22d3ee;
    padding: 0.8rem 1rem;
    border-radius: 0 6px 6px 0;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.8rem;
    color: #94a3b8;
    margin: 0.5rem 0;
    line-height: 1.5;
}

.disclaimer-box {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 0.6rem 1rem;
    font-size: 0.72rem;
    color: #475569;
    font-family: 'IBM Plex Mono', monospace;
}

.section-title {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.15em;
    color: #475569;
    text-transform: uppercase;
    margin-bottom: 0.75rem;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid #1e293b;
}
</style>
""", unsafe_allow_html=True)


# ── Constants ────────────────────────────────────────────────────────────────
HISTORY_PATH = "nrs1_history.jsonl"
REPORT_PATH  = "nrs1_report.md"
AUDIT_PATH   = "nrs1_audit.jsonl"

LABEL_COLORS = {
    "STRONG_MISMATCH":       "#ef4444",
    "MODERATE_MISMATCH":     "#f97316",
    "WEAK_MISMATCH":         "#eab308",
    "ALIGNED":               "#22c55e",
    "INSUFFICIENT_EVIDENCE": "#94a3b8",
}

LABEL_DESCRIPTIONS = {
    "STRONG_MISMATCH":       "Narrative far ahead of engineering reality. Market may be pricing fiction.",
    "MODERATE_MISMATCH":     "Material divergence. Worth tracking as evidence evolves.",
    "WEAK_MISMATCH":         "Minor gap. Within normal uncertainty range.",
    "ALIGNED":               "Narrative and reality roughly consistent.",
    "INSUFFICIENT_EVIDENCE": "Not enough technical evidence to score.",
}


# ── Data loading ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)   # refresh every 60 seconds
def load_history() -> pd.DataFrame:
    """Load nrs1_history.jsonl into a DataFrame."""
    if not os.path.exists(HISTORY_PATH):
        return pd.DataFrame()
    rows = []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.sort_values("ts")
    return df


@st.cache_data(ttl=60)
def load_report() -> str:
    """Load nrs1_report.md."""
    if not os.path.exists(REPORT_PATH):
        return ""
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        return f.read()


@st.cache_data(ttl=60)
def load_audit_count() -> int:
    """Count lines in audit trail."""
    if not os.path.exists(AUDIT_PATH):
        return 0
    with open(AUDIT_PATH, "r") as f:
        return sum(1 for _ in f)


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚙ Engine Parameters")

    alpha = st.slider("α — NR weight", 0.0, 1.0, 0.5, 0.05,
                       help="Weight on Narrative vs Reality gap")
    beta  = st.slider("β — MR weight", 0.0, 1.0, 0.5, 0.05,
                       help="Weight on Market vs Reality gap")

    if abs(alpha + beta - 1.0) > 1e-9:
        st.error(f"α + β = {alpha+beta:.2f} ≠ 1.0")
    else:
        st.success("α + β = 1.00 ✓")

    st.divider()

    st.markdown("### 🔍 Filter")
    df_all = load_history()
    tickers_available = sorted(df_all["ticker"].unique().tolist()) if not df_all.empty and "ticker" in df_all.columns else []
    selected_tickers = st.multiselect("Tickers", tickers_available,
                                       default=tickers_available)

    st.divider()

    st.markdown("### 📋 About")
    st.caption("""**NRS-1 v2**  
Narrative-Reality Mispricing Workbench  
  
Gap Index detects divergence between market narratives and engineering feasibility.  
  
*Not investment advice.*""")

    audit_count = load_audit_count()
    st.metric("Audit trail entries", audit_count)
    st.metric("History records", len(df_all))


# ── Header ────────────────────────────────────────────────────────────────────

st.markdown("""
<div class="nrs-header">
  <div class="nrs-title">⚡ NRS-1 Mispricing Workbench</div>
  <div class="nrs-subtitle">NARRATIVE · REALITY · MARKET — GAP INDEX DASHBOARD</div>
</div>
""", unsafe_allow_html=True)


# ── Load + filter data ─────────────────────────────────────────────────────────

df = load_history()
if not df.empty and selected_tickers:
    df = df[df["ticker"].isin(selected_tickers)]


# ── No data state ─────────────────────────────────────────────────────────────

if df.empty:
    st.warning("No history data found. Run `python nrs1_v2.py --stub` first to generate data.")
    st.code("python nrs1_v2.py --stub", language="bash")
    st.stop()


# ── Latest run summary ─────────────────────────────────────────────────────────

latest = df.iloc[-1]
label  = latest.get("gap_label", "UNKNOWN")
gi     = float(latest.get("gap_index", 0))
color  = LABEL_COLORS.get(label, "#94a3b8")

st.markdown("#### Latest Analysis")

col_main, col_right = st.columns([2, 1])

with col_main:
    # Gap index + label
    st.markdown(f"""
    <div style="display:flex; align-items:center; gap:1.5rem; margin-bottom:1rem;">
      <div>
        <div style="font-family:'IBM Plex Mono',monospace; font-size:3rem; font-weight:700;
             color:{color}; line-height:1; letter-spacing:-0.03em;">{gi:.4f}</div>
        <div style="font-size:0.75rem; color:#64748b; font-family:'IBM Plex Mono',monospace;
             margin-top:0.2rem;">GAP INDEX</div>
      </div>
      <div>
        <span class="gap-label-pill {label}">{label.replace("_", " ")}</span>
        <div style="font-size:0.78rem; color:#64748b; margin-top:0.4rem; max-width:280px;">
          {LABEL_DESCRIPTIONS.get(label, "")}
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Score bars
    scores = [
        ("N_score", latest.get("n_score", 0), "#22d3ee",  "Narrative strength"),
        ("R_score", latest.get("r_score", 0), "#f97316",  "Engineering reality"),
        ("M_implied", latest.get("m_implied", 0), "#a78bfa", "Market implied"),
        ("NR_gap",  latest.get("nr_gap", 0),  "#ef4444",  "Narrative–Reality gap"),
        ("MR_gap",  latest.get("mr_gap", 0),  "#ec4899",  "Market–Reality gap"),
    ]
    for sname, sval, scolor, stip in scores:
        if sval is not None:
            pct = max(0, min(100, float(sval) * 100))
            st.markdown(f"""
            <div class="score-row" title="{stip}">
              <div class="score-label">{sname}</div>
              <div class="score-bar-bg">
                <div class="score-bar-fill" style="width:{pct}%; background:{scolor};"></div>
              </div>
              <div class="score-value">{float(sval):.4f}</div>
            </div>""", unsafe_allow_html=True)

with col_right:
    st.markdown(f"""
    <div style="font-family:'IBM Plex Mono',monospace; font-size:0.72rem; color:#64748b; margin-bottom:0.5rem;">
      LATEST CLAIM
    </div>""", unsafe_allow_html=True)
    claim_text = latest.get("claim", "—")
    ticker_text = latest.get("ticker", "—")
    ts_text = latest["ts"].strftime("%Y-%m-%d %H:%M UTC") if hasattr(latest["ts"], "strftime") else str(latest["ts"])[:16]
    st.markdown(f"""<div class="claim-box">
      <strong style="color:#22d3ee;">{ticker_text}</strong><br>
      {claim_text}<br><br>
      <span style="color:#475569;">{ts_text}</span>
    </div>""", unsafe_allow_html=True)

    # Quick stats
    strong_count = len(df[df["gap_label"] == "STRONG_MISMATCH"])
    mod_count    = len(df[df["gap_label"] == "MODERATE_MISMATCH"])
    avg_gap      = df["gap_index"].mean()

    st.markdown(f"""
    <div style="margin-top:0.8rem; font-family:'IBM Plex Mono',monospace; font-size:0.72rem;">
      <div style="display:flex; justify-content:space-between; padding:0.3rem 0; border-bottom:1px solid #1e293b;">
        <span style="color:#64748b;">STRONG MISMATCH</span>
        <span style="color:#ef4444; font-weight:600;">{strong_count}</span>
      </div>
      <div style="display:flex; justify-content:space-between; padding:0.3rem 0; border-bottom:1px solid #1e293b;">
        <span style="color:#64748b;">MODERATE</span>
        <span style="color:#f97316; font-weight:600;">{mod_count}</span>
      </div>
      <div style="display:flex; justify-content:space-between; padding:0.3rem 0;">
        <span style="color:#64748b;">AVG GAP (14d)</span>
        <span style="color:#e2e8f0; font-weight:600;">{avg_gap:.3f}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)


st.divider()


# ── Historical trend chart ─────────────────────────────────────────────────────

st.markdown("#### 📈 Gap Index — Historical Trend")

try:
    import plotly.graph_objects as go

    fig = go.Figure()

    # Background shading for STRONG_MISMATCH zone
    fig.add_hrect(y0=0.60, y1=1.0,
                  fillcolor="rgba(239,68,68,0.05)",
                  line_width=0, annotation_text="STRONG MISMATCH",
                  annotation_font_size=10, annotation_font_color="#ef4444",
                  annotation_position="top left")
    fig.add_hrect(y0=0.35, y1=0.60,
                  fillcolor="rgba(249,115,22,0.05)",
                  line_width=0)
    fig.add_hrect(y0=0.15, y1=0.35,
                  fillcolor="rgba(234,179,8,0.05)",
                  line_width=0)

    # Threshold lines
    for y, color, label in [(0.60, "#ef4444", "Strong"), (0.35, "#f97316", "Moderate"), (0.15, "#eab308", "Weak")]:
        fig.add_hline(y=y, line_dash="dot", line_color=color, line_width=1,
                      annotation_text=label, annotation_font_size=9,
                      annotation_font_color=color)

    # Gap Index line
    fig.add_trace(go.Scatter(
        x=df["ts"], y=df["gap_index"],
        mode="lines+markers",
        name="Gap Index",
        line=dict(color="#22d3ee", width=2.5),
        marker=dict(size=7, color=[LABEL_COLORS.get(l, "#22d3ee") for l in df["gap_label"]],
                    line=dict(width=1.5, color="#0f172a")),
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Gap: %{y:.4f}<extra></extra>",
    ))

    # NR_gap and MR_gap as lighter traces
    if "nr_gap" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["ts"], y=df["nr_gap"],
            mode="lines", name="NR gap",
            line=dict(color="#f97316", width=1, dash="dot"),
            hovertemplate="NR: %{y:.4f}<extra></extra>",
        ))
    if "mr_gap" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["ts"], y=df["mr_gap"],
            mode="lines", name="MR gap",
            line=dict(color="#a78bfa", width=1, dash="dot"),
            hovertemplate="MR: %{y:.4f}<extra></extra>",
        ))

    fig.update_layout(
        plot_bgcolor="#0a0a0a",
        paper_bgcolor="#0a0a0a",
        font=dict(family="IBM Plex Mono", size=11, color="#94a3b8"),
        xaxis=dict(gridcolor="#1e293b", showgrid=True, zeroline=False),
        yaxis=dict(gridcolor="#1e293b", showgrid=True, zeroline=False,
                   range=[-0.05, 1.05], tickformat=".2f"),
        legend=dict(bgcolor="#111827", bordercolor="#1e293b", borderwidth=1),
        margin=dict(l=20, r=20, t=20, b=20),
        height=320,
        hovermode="x unified",
    )

    st.plotly_chart(fig, use_container_width=True)

except ImportError:
    # Fallback to Streamlit native chart if plotly not installed
    chart_df = df[["ts","gap_index","nr_gap","mr_gap"]].copy()
    chart_df = chart_df.set_index("ts")
    st.line_chart(chart_df, color=["#22d3ee","#f97316","#a78bfa"])


# ── History table ─────────────────────────────────────────────────────────────

st.markdown("#### 📋 Analysis History")

display_cols = ["ts","ticker","gap_index","gap_label","n_score","r_score","m_implied","claim"]
display_cols = [c for c in display_cols if c in df.columns]
df_display = df[display_cols].copy().sort_values("ts", ascending=False)
df_display["ts"] = df_display["ts"].dt.strftime("%Y-%m-%d %H:%M")

def color_label(val):
    colors = {
        "STRONG_MISMATCH":    "background-color:#fee2e2; color:#991b1b; font-weight:600",
        "MODERATE_MISMATCH":  "background-color:#fff7ed; color:#92400e; font-weight:600",
        "WEAK_MISMATCH":      "background-color:#fefce8; color:#854d0e; font-weight:600",
        "ALIGNED":            "background-color:#dcfce7; color:#166534; font-weight:600",
    }
    return colors.get(val, "")

styled = df_display.style.map(color_label, subset=["gap_label"])
st.dataframe(styled, use_container_width=True, height=280)


st.divider()


# ── Full report ─────────────────────────────────────────────────────────────────

report_content = load_report()
if report_content:
    with st.expander("📄 Latest Logic Hedge Report (full)", expanded=False):
        st.markdown(report_content)
else:
    st.info("No report file found. Run `python nrs1_v2.py --stub` to generate one.")


# ── How to update data ─────────────────────────────────────────────────────────

with st.expander("⚙ How to add new analysis data", expanded=False):
    st.markdown("""
**Run the pipeline locally** to add new data:
```bash
# Stub mode (no API key needed):
python nrs1_v2.py --stub

# Live mode (requires ANTHROPIC_API_KEY):
python nrs1_v2.py
```
Then commit and push `nrs1_history.jsonl` and `nrs1_report.md` to GitHub.  
The dashboard on Streamlit Cloud will update automatically within ~60 seconds.

**To run automatically every day at 8am:**  
→ Windows: Task Scheduler → run `nrs1_v2.py` daily at 08:00  
→ Cloud: GitHub Actions workflow (free, no computer needed)
""")


# ── Disclaimer ────────────────────────────────────────────────────────────────

st.markdown("""
<div class="disclaimer-box">
  ⚠ DISCLAIMER: NRS-1 is a logic-consistency analysis tool only. Gap Index scores are
  experimental, uncalibrated, and do not constitute investment advice, trading signals,
  or recommendations to buy or sell any security. All scores based on Iteration 1 stubs
  unless explicitly running with live LLM data.
</div>
""", unsafe_allow_html=True)
