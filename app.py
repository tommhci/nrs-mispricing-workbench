"""
NRS-1 v3 Dashboard — Signal Monitor
=====================================
Three-tab layout:
  ◈  Overview  — current Gap Index + claim card
  ◷  Timeline  — N/R divergence chart (the core visualization)
  ⊙  Analysis  — evidence quality + source distribution + full log

Data: SQLite via db.py (auto-migrated from nrs1_history.jsonl on first load).
"""

import math, os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import db

st.set_page_config(
    page_title="NRS-1 · Signal Monitor",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=Outfit:wght@300;400;500;600&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500&display=swap');

:root {
  --bg:#0c0c0d; --surface:#111113; --surface2:#161618;
  --border:#232326; --border2:#2a2a2e;
  --text1:#f4f4f5; --text2:#a1a1aa; --text3:#52525b;
  --amber:#f59e0b; --red:#dc2626; --orange:#ea580c;
  --yellow:#ca8a04; --green:#16a34a; --blue:#3b82f6;
}
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"],
.main, section.main > div {
    font-family:'DM Sans',system-ui,sans-serif !important;
    background-color:var(--bg) !important; color:var(--text1) !important;
}
[data-testid="stSidebar"], [data-testid="stSidebar"] > div {
    background-color:var(--surface) !important;
    border-right:1px solid var(--border) !important;
}
[data-testid="stSidebar"] label, [data-testid="stSidebar"] p,
[data-testid="stSidebar"] span { color:var(--text2) !important; font-size:0.8rem !important; }
/* Tabs */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    background:transparent !important; border-bottom:1px solid var(--border) !important; gap:0;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
    font-family:'DM Mono',monospace !important; font-size:0.73rem !important;
    color:var(--text3) !important; letter-spacing:0.07em !important;
    background:transparent !important; border:none !important;
    padding:0.55rem 1.2rem !important; border-bottom:2px solid transparent !important;
}
[data-testid="stTabs"] [aria-selected="true"] {
    color:var(--amber) !important; border-bottom:2px solid var(--amber) !important;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { display:none !important; }
/* Chart subplot titles */
.subplot-title { font-family:'DM Mono',monospace; font-size:0.65rem;
    color:var(--text3); letter-spacing:0.12em; text-transform:uppercase;
    padding-bottom:0.4rem; border-bottom:1px solid var(--border); margin-bottom:0.8rem; }
hr { border-color:var(--border) !important; margin:1.2rem 0 !important; }
#MainMenu, footer, header { visibility:hidden; }
.block-container { padding-top:1.8rem !important; padding-left:2rem !important;
    padding-right:2rem !important; max-width:1440px !important; }
/* Gap display */
.gap-number { font-family:'DM Mono',monospace; font-size:4rem; font-weight:500;
    line-height:1; letter-spacing:-0.04em; }
.gap-number.STRONG_MISMATCH   { color:var(--red); }
.gap-number.MODERATE_MISMATCH { color:var(--orange); }
.gap-number.WEAK_MISMATCH     { color:var(--yellow); }
.gap-number.ALIGNED           { color:var(--green); }
.gap-number.INSUFFICIENT_EVIDENCE { color:var(--text3); }
.label-row { display:flex; align-items:center; gap:0.5rem; margin-top:0.4rem; }
.label-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.label-dot.STRONG_MISMATCH   { background:var(--red); }
.label-dot.MODERATE_MISMATCH { background:var(--orange); }
.label-dot.WEAK_MISMATCH     { background:var(--yellow); }
.label-dot.ALIGNED           { background:var(--green); }
.label-name { font-family:'DM Mono',monospace; font-size:0.72rem; font-weight:500;
    letter-spacing:0.06em; color:var(--text1); }
.label-desc { font-size:0.78rem; color:var(--text2); margin-top:0.3rem;
    line-height:1.55; max-width:360px; }
/* Score bars */
.score-grid { display:grid; gap:0.5rem; margin:1rem 0; }
.score-item { display:grid; grid-template-columns:80px 1fr 52px; align-items:center; gap:0.5rem; }
.score-key { font-family:'DM Mono',monospace; font-size:0.67rem; color:var(--text3); text-align:right; }
.score-track { height:3px; background:var(--border2); border-radius:2px; overflow:hidden; }
.score-fill { height:100%; border-radius:2px; }
.is-gap  { background:var(--red); }
.is-n    { background:var(--amber); }
.is-r    { background:var(--blue); }
.is-m    { background:#6366f1; }
.score-val { font-family:'DM Mono',monospace; font-size:0.67rem; color:var(--text2); text-align:right; }
/* Claim card */
.claim-card { background:var(--surface); border:1px solid var(--border);
    border-radius:8px; padding:1rem 1.2rem; }
.claim-ticker { font-family:'DM Mono',monospace; font-size:0.7rem; color:var(--amber);
    letter-spacing:0.1em; margin-bottom:0.45rem; }
.claim-text  { font-size:0.87rem; color:var(--text1); line-height:1.58; margin-bottom:0.65rem; }
.claim-meta  { font-family:'DM Mono',monospace; font-size:0.63rem; color:var(--text3); }
/* Sidebar stats */
.stat-row { display:flex; justify-content:space-between; padding:0.5rem 0;
    border-bottom:1px solid var(--border); }
.stat-row:last-child { border-bottom:none; }
.stat-label { font-family:'DM Mono',monospace; font-size:0.68rem; color:var(--text3); letter-spacing:0.05em; }
.stat-value { font-family:'DM Mono',monospace; font-size:0.68rem; color:var(--text1); font-weight:500; }
.stat-value.warn { color:var(--red); } .stat-value.ok { color:var(--green); }
.page-title { font-family:'Outfit',sans-serif; font-size:1.4rem; font-weight:600;
    color:var(--text1); letter-spacing:-0.02em;
    border-left:3px solid var(--amber); padding-left:0.85rem; margin-bottom:0.15rem; }
.page-sub { font-family:'DM Mono',monospace; font-size:0.66rem; color:var(--text3);
    letter-spacing:0.12em; padding-left:1.1rem; text-transform:uppercase; }
.section-head { font-family:'DM Mono',monospace; font-size:0.63rem; font-weight:500;
    letter-spacing:0.14em; color:var(--text3); text-transform:uppercase;
    padding-bottom:0.45rem; border-bottom:1px solid var(--border); margin-bottom:0.9rem; }
.disclaimer { font-family:'DM Mono',monospace; font-size:0.63rem; color:var(--text3);
    border-top:1px solid var(--border); padding-top:0.9rem; line-height:1.7; }
</style>
""", unsafe_allow_html=True)


# ── Constants ──────────────────────────────────────────────────────────────────
REPORT_PATH = "nrs1_report.md"
AUDIT_PATH  = "nrs1_audit.jsonl"

DOT_COLOR = {
    "STRONG_MISMATCH":       "#dc2626",
    "MODERATE_MISMATCH":     "#ea580c",
    "WEAK_MISMATCH":         "#ca8a04",
    "ALIGNED":               "#16a34a",
    "INSUFFICIENT_EVIDENCE": "#52525b",
}
LABEL_DESC = {
    "STRONG_MISMATCH":       "Narrative far ahead of engineering reality.",
    "MODERATE_MISMATCH":     "Material divergence between narrative and reality.",
    "WEAK_MISMATCH":         "Minor gap — within uncertainty range.",
    "ALIGNED":               "Narrative and engineering roughly consistent.",
    "INSUFFICIENT_EVIDENCE": "Insufficient evidence to compute a score.",
}
EVIDENCE_COLOR = {
    "strong": "#16a34a", "moderate": "#f59e0b",
    "weak": "#ea580c", "insufficient": "#52525b",
}
TIER_COLOR = {1: "#16a34a", 2: "#f59e0b", 3: "#52525b"}
TIER_LABEL = {1: "T1 · Primary", 2: "T2 · Expert", 3: "T3 · Media"}


# ── Safe number formatter ──────────────────────────────────────────────────────
def fmt(x, d: int = 4) -> str:
    if x is None:
        return "—"
    try:
        f = float(x)
        return "—" if math.isnan(f) else f"{f:.{d}f}"
    except Exception:
        return "—"


# ── Empty chart placeholder ────────────────────────────────────────────────────
def _empty_fig(msg: str = "No data", h: int = 200) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
                       font=dict(color="#52525b", size=12, family="DM Mono"),
                       showarrow=False)
    fig.update_layout(plot_bgcolor="#0c0c0d", paper_bgcolor="#0c0c0d",
                      height=h, margin=dict(l=0, r=0, t=0, b=0),
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig


# ── Chart helpers ──────────────────────────────────────────────────────────────
def make_divergence_chart(df: pd.DataFrame) -> go.Figure:
    """
    Core visualization: N/R divergence with Gap Index subplot.

    Visual logic:
      Blue filled area  (0 → R_score)  =  Reality layer
      Amber filled area (R_score → N)  =  Divergence zone
      When N > R: amber zone visible above blue  → MISMATCH (signal)
      When N ≈ R: amber collapses to zero        → ALIGNED (no signal)
      Gap Index bar chart below (colored by label) confirms magnitude.
    """
    df_s = df.dropna(subset=["n_score", "r_score", "ts"]).sort_values("ts")
    if len(df_s) < 2:
        return _empty_fig("Need ≥ 2 records to draw divergence chart", h=480)

    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.68, 0.32],
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=("N · Narrative  vs  R · Reality", "Gap Index"),
    )

    # R_score: blue fill from zero — "reality floor"
    fig.add_trace(go.Scatter(
        x=df_s["ts"], y=df_s["r_score"],
        fill="tozeroy", fillcolor="rgba(59,130,246,0.11)",
        mode="lines", name="R · Reality",
        line=dict(color="#3b82f6", width=1.5),
        hovertemplate="<b>Reality</b>: %{y:.4f}<extra></extra>",
    ), row=1, col=1)

    # N_score: amber fill from R_score — gap zone is visible when N > R
    fig.add_trace(go.Scatter(
        x=df_s["ts"], y=df_s["n_score"],
        fill="tonexty", fillcolor="rgba(245,158,11,0.15)",
        mode="lines", name="N · Narrative",
        line=dict(color="#f59e0b", width=1.5),
        hovertemplate="<b>Narrative</b>: %{y:.4f}<extra></extra>",
    ), row=1, col=1)

    # Neutral 0.5 reference line
    fig.add_hline(y=0.5, row=1, col=1,
                  line=dict(color="#2a2a2e", width=1, dash="dot"))

    # Gap Index bars — colored by label
    bar_colors = [DOT_COLOR.get(str(l), "#52525b")
                  for l in df_s.get("gap_label", pd.Series(dtype=str))]
    fig.add_trace(go.Bar(
        x=df_s["ts"],
        y=df_s["gap_index"].fillna(0),
        marker_color=bar_colors,
        name="Gap Index",
        hovertemplate="<b>Gap</b>: %{y:.4f}<extra></extra>",
    ), row=2, col=1)

    # Threshold lines on gap subplot
    for y_val, col, lbl in [(0.60, "#dc2626", "Strong"),
                             (0.35, "#ea580c", "Mod."),
                             (0.15, "#ca8a04", "Weak")]:
        fig.add_hline(y=y_val, row=2, col=1,
                      line=dict(color=col, width=0.5, dash="dot"),
                      annotation_text=lbl,
                      annotation_font=dict(size=8, color=col),
                      annotation_position="right")

    _chart_layout(fig, height=480)
    fig.update_yaxes(range=[-0.02, 1.08], row=1, col=1)
    fig.update_yaxes(range=[0, 1.05], row=2, col=1)
    # Dim subplot titles
    for ann in fig.layout.annotations:
        ann.font.size = 10
        ann.font.color = "#52525b"
    return fig


def make_evidence_donut(df: pd.DataFrame) -> go.Figure:
    if df.empty or "evidence" not in df.columns:
        return _empty_fig("No evidence data", h=220)
    counts = df["evidence"].dropna().value_counts()
    if counts.empty:
        return _empty_fig("No evidence data", h=220)
    labels = counts.index.tolist()
    colors = [EVIDENCE_COLOR.get(l, "#52525b") for l in labels]
    fig = go.Figure(go.Pie(
        labels=labels, values=counts.values.tolist(), hole=0.62,
        marker=dict(colors=colors, line=dict(color="#0c0c0d", width=2)),
        textfont=dict(family="DM Mono", size=9),
        hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
    ))
    _chart_layout(fig, height=220)
    fig.update_layout(showlegend=True,
                      legend=dict(font=dict(size=9, color="#71717a"),
                                  bgcolor="rgba(0,0,0,0)"))
    return fig


def make_label_bar(df: pd.DataFrame) -> go.Figure:
    if df.empty or "gap_label" not in df.columns:
        return _empty_fig("No data", h=220)
    ORDER = ["STRONG_MISMATCH", "MODERATE_MISMATCH", "WEAK_MISMATCH",
             "ALIGNED", "INSUFFICIENT_EVIDENCE"]
    counts = df["gap_label"].value_counts()
    labels = [l for l in ORDER if l in counts.index]
    short  = [l.replace("_MISMATCH", "").replace("_", " ") for l in labels]
    colors = [DOT_COLOR.get(l, "#52525b") for l in labels]
    fig = go.Figure(go.Bar(x=short, y=[counts[l] for l in labels],
                           marker_color=colors,
                           hovertemplate="%{x}: %{y}<extra></extra>"))
    _chart_layout(fig, height=220)
    return fig


def make_tier_bar(df: pd.DataFrame) -> go.Figure:
    """Source tier distribution as a stacked bar over time (by date)."""
    if df.empty or "source_tier" not in df.columns or len(df) < 3:
        return _empty_fig("Need ≥ 3 records", h=180)
    df_t = df.copy()
    df_t["date"] = df_t["ts"].dt.date.astype(str)
    fig = go.Figure()
    for tier in [1, 2, 3]:
        chunk = df_t[df_t["source_tier"] == tier].groupby("date").size()
        if chunk.empty:
            continue
        fig.add_trace(go.Bar(x=chunk.index, y=chunk.values,
                             name=TIER_LABEL[tier],
                             marker_color=TIER_COLOR[tier]))
    fig.update_layout(barmode="stack")
    _chart_layout(fig, height=180)
    fig.update_layout(legend=dict(font=dict(size=9, color="#71717a"),
                                  bgcolor="rgba(0,0,0,0)",
                                  orientation="h", y=-0.35))
    return fig


def _chart_layout(fig: go.Figure, height: int = 300) -> None:
    """Apply shared dark theme layout to any figure."""
    fig.update_layout(
        plot_bgcolor="#0c0c0d", paper_bgcolor="#0c0c0d",
        font=dict(family="DM Mono, monospace", size=10, color="#52525b"),
        height=height,
        hovermode="x unified",
        margin=dict(l=0, r=56, t=24, b=16),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=9, color="#71717a"),
                    orientation="h", y=-0.12),
    )
    fig.update_xaxes(gridcolor="#161618", showgrid=True, zeroline=False,
                     tickfont=dict(size=9), showline=False)
    fig.update_yaxes(gridcolor="#161618", showgrid=True, zeroline=False,
                     tickformat=".2f", tickfont=dict(size=9))


# ── HTML table (Analysis tab) ─────────────────────────────────────────────────
def render_table_html(rows: list) -> str:
    TD = ("padding:7px 10px;font-size:0.71rem;color:#a1a1aa;"
          "border-bottom:1px solid #1a1a1c;font-family:'DM Mono',monospace;"
          "white-space:nowrap;")
    TH = ("text-align:left;padding:6px 10px;color:#3f3f46;font-size:0.63rem;"
          "letter-spacing:.1em;border-bottom:1px solid #232326;font-weight:500;"
          "white-space:nowrap;background:#161618;")
    headers = ["Date", "Ticker", "Gap", "Label", "N", "R", "M", "Evidence",
               "Tier", "Source", "Claim"]
    thead = "".join(f'<th style="{TH}">{h}</th>' for h in headers)
    tbody = ""
    for i, r in enumerate(rows):
        lbl   = r.get("gap_label", "")
        dc    = DOT_COLOR.get(lbl, "#52525b")
        short = lbl.replace("_MISMATCH", "").replace("_", " ")
        bg    = "rgba(255,255,255,0.012)" if i % 2 == 0 else "transparent"

        ts_val = r.get("ts", "")
        if hasattr(ts_val, "strftime"):
            ts_val = ts_val.strftime("%m-%d %H:%M")

        tier_val = r.get("source_tier", "")
        tc = TIER_COLOR.get(int(tier_val), "#52525b") if tier_val != "" else "#52525b"
        td = f"T{int(tier_val)}" if tier_val != "" else "—"
        src = str(r.get("source_name", "—"))[:10]
        claim = str(r.get("claim", ""))
        claim_disp = claim[:52] + ("…" if len(claim) > 52 else "")

        cells = "".join([
            f'<td style="{TD}">{ts_val}</td>',
            f'<td style="{TD};color:#f59e0b;font-weight:500;">{r.get("ticker","—")}</td>',
            f'<td style="{TD};color:{dc};font-weight:600;">{fmt(r.get("gap_index"))}</td>',
            f'<td style="{TD}"><span style="display:inline-flex;align-items:center;gap:4px;">'
            f'<span style="width:5px;height:5px;border-radius:50%;background:{dc};flex-shrink:0;"></span>'
            f'<span style="color:{dc};font-size:0.63rem;">{short}</span></span></td>',
            f'<td style="{TD}">{fmt(r.get("n_score"), 3)}</td>',
            f'<td style="{TD}">{fmt(r.get("r_score"), 3)}</td>',
            f'<td style="{TD}">{fmt(r.get("m_implied"), 3)}</td>',
            f'<td style="{TD}">{r.get("evidence","—")}</td>',
            f'<td style="{TD};color:{tc};font-weight:500;">{td}</td>',
            f'<td style="{TD};color:#71717a;">{src}</td>',
            f'<td style="{TD};color:#52525b;max-width:180px;overflow:hidden;'
            f'text-overflow:ellipsis;">{claim_disp}</td>',
        ])
        tbody += f'<tr style="background:{bg};">{cells}</tr>'

    return (f'<div style="overflow-x:auto;border:1px solid #232326;border-radius:8px;'
            f'background:#111113;max-height:340px;overflow-y:auto;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>{thead}</tr></thead>'
            f'<tbody>{tbody}</tbody></table></div>')


# ── Data loading ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_data() -> pd.DataFrame:
    return db.load_df()


@st.cache_data(ttl=60)
def load_report() -> str:
    if not os.path.exists(REPORT_PATH):
        return ""
    return open(REPORT_PATH, encoding="utf-8").read()


@st.cache_data(ttl=60)
def audit_count() -> int:
    if not os.path.exists(AUDIT_PATH):
        return 0
    return sum(1 for _ in open(AUDIT_PATH))


# ── Sidebar ───────────────────────────────────────────────────────────────────
df_all = load_data()
tickers_avail = (sorted(df_all["ticker"].dropna().unique().tolist())
                 if not df_all.empty and "ticker" in df_all.columns else [])

with st.sidebar:
    st.markdown('<div class="section-head">Filter</div>', unsafe_allow_html=True)
    selected_tickers = st.multiselect(
        "Tickers", tickers_avail, default=tickers_avail,
        label_visibility="collapsed",
    )
    st.markdown('<hr/>', unsafe_allow_html=True)
    st.markdown('<div class="section-head">Gap Index Weights</div>',
                unsafe_allow_html=True)
    alpha = st.slider("α  NR weight", 0.0, 1.0, 0.5, 0.05)
    beta  = st.slider("β  MR weight", 0.0, 1.0, 0.5, 0.05)
    ok    = abs(alpha + beta - 1.0) < 1e-9
    st.markdown(
        f'<p style="font-family:DM Mono,monospace;font-size:0.72rem;'
        f'color:{"#16a34a" if ok else "#dc2626"};margin-top:.2rem;">'
        f'α + β = {alpha+beta:.2f}  {"✓" if ok else "✗"}</p>',
        unsafe_allow_html=True)
    st.markdown('<hr/>', unsafe_allow_html=True)
    stats = db.get_stats()
    ac = audit_count()
    strong_n = stats.get("by_label", {}).get("STRONG_MISMATCH", 0)
    st.markdown(f"""
    <div class="stat-row">
      <span class="stat-label">RECORDS</span>
      <span class="stat-value">{stats.get('total', len(df_all))}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">AUDIT ENTRIES</span>
      <span class="stat-value">{ac}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">STRONG MISMATCH</span>
      <span class="stat-value {'warn' if strong_n > 0 else 'ok'}">{strong_n}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">AVG GAP INDEX</span>
      <span class="stat-value">{fmt(stats.get('avg_gi'), 4)}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">VERSION</span>
      <span class="stat-value ok">NRS-1 v3</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">LLM</span>
      <span class="stat-value">GLM (Zhipu AI)</span>
    </div>
    """, unsafe_allow_html=True)
    st.markdown(
        '<p style="font-family:DM Mono,monospace;font-size:0.6rem;color:#3f3f46;'
        'margin-top:1.2rem;">Not investment advice.</p>',
        unsafe_allow_html=True)


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="page-title">NRS-1 · Signal Monitor</div>
<div class="page-sub">Narrative · Reality · Market — Gap Index</div>
<hr/>
""", unsafe_allow_html=True)

# ── Filter data ───────────────────────────────────────────────────────────────
df = df_all.copy()
if not df.empty and selected_tickers:
    df = df[df["ticker"].isin(selected_tickers)]

if df.empty:
    st.markdown(
        '<p style="color:#71717a;font-family:DM Mono,monospace;font-size:0.82rem;">'
        'No data. Run: <code>python nrs1_v3.py --stub</code> to generate sample data.</p>',
        unsafe_allow_html=True)
    st.stop()

latest = df.iloc[-1]

# ── TABS ──────────────────────────────────────────────────────────────────────
tab_ov, tab_tl, tab_an = st.tabs([
    "◈  Overview",
    "◷  Timeline",
    "⊙  Analysis",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════
with tab_ov:
    col_left, col_right = st.columns([6, 4], gap="large")

    with col_left:
        st.markdown('<div class="section-head">Current Signal</div>',
                    unsafe_allow_html=True)

        label = str(latest.get("gap_label", "UNKNOWN"))
        gi    = float(latest.get("gap_index") or 0)

        st.markdown(f"""
        <div class="gap-number {label}">{gi:.4f}</div>
        <div class="label-row">
          <div class="label-dot {label}"></div>
          <span class="label-name">{label.replace("_", " ")}</span>
        </div>
        <p class="label-desc">{LABEL_DESC.get(label, "")}</p>
        """, unsafe_allow_html=True)

        # Evidence ceiling warning
        ev_ceiling  = str(latest.get("ev_ceiling", "") or "")
        ev_strength = str(latest.get("evidence",   "") or "")
        if ev_ceiling and ev_strength and ev_ceiling != ev_strength:
            src_tier = latest.get("source_tier", "")
            st.markdown(
                f'<p style="font-family:DM Mono,monospace;font-size:0.67rem;'
                f'color:#ea580c;margin:0.6rem 0;">'
                f'⚠ Evidence ceiling applied · Tier {src_tier} source · '
                f'capped at <strong>{ev_strength}</strong></p>',
                unsafe_allow_html=True)

        # Score bars
        score_items = [
            ("N_score",   latest.get("n_score",   0), "is-n"),
            ("R_score",   latest.get("r_score",   0), "is-r"),
            ("M_implied", latest.get("m_implied", 0), "is-m"),
            ("NR_gap",    latest.get("nr_gap",    0), "is-gap"),
            ("MR_gap",    latest.get("mr_gap",    0), "is-gap"),
        ]
        bars = '<div class="score-grid">'
        for key, val, css in score_items:
            v   = float(val) if (val is not None and
                                  not (isinstance(val, float) and math.isnan(val))) else 0.0
            pct = max(0, min(100, v * 100))
            bars += (f'<div class="score-item">'
                     f'<div class="score-key">{key}</div>'
                     f'<div class="score-track">'
                     f'<div class="score-fill {css}" style="width:{pct}%"></div>'
                     f'</div>'
                     f'<div class="score-val">{v:.4f}</div>'
                     f'</div>')
        bars += '</div>'
        st.markdown(bars, unsafe_allow_html=True)

    with col_right:
        st.markdown('<div class="section-head">Latest Claim</div>',
                    unsafe_allow_html=True)

        ticker   = str(latest.get("ticker",   "—"))
        claim    = str(latest.get("claim",    "—"))
        ts_raw   = latest.get("ts")
        ts_str   = (ts_raw.strftime("%Y-%m-%d %H:%M UTC")
                    if hasattr(ts_raw, "strftime") else str(ts_raw)[:16])
        evidence = str(latest.get("evidence", "—"))
        mode     = str(latest.get("mode",     "—"))

        src_tier  = latest.get("source_tier", "")
        src_name  = str(latest.get("source_name", "") or "")
        doc_type  = str(latest.get("doc_type",  "") or "")
        tc        = TIER_COLOR.get(int(src_tier), "#52525b") if src_tier != "" else "#52525b"
        tl        = {1: "Tier 1 · Primary Filing",
                     2: "Tier 2 · Expert Analysis",
                     3: "Tier 3 · General Media"}.get(
                         int(src_tier) if src_tier != "" else 0, "")
        tier_badge = (f'<div style="font-family:DM Mono,monospace;font-size:0.63rem;'
                      f'color:{tc};margin-top:0.3rem;">'
                      f'{src_name} · {tl} · {doc_type}</div>') if src_tier != "" else ""

        st.markdown(f"""
        <div class="claim-card">
          <div class="claim-ticker">{ticker}</div>
          <div class="claim-text">{claim}</div>
          <div class="claim-meta">{ts_str} · {evidence} · {mode}</div>
          {tier_badge}
        </div>
        """, unsafe_allow_html=True)

        # Distribution stats
        by_lbl  = df["gap_label"].value_counts().to_dict()
        avg_gi  = df["gap_index"].mean()
        st.markdown(f"""
        <div style="margin-top:1.1rem;">
        <div class="stat-row">
          <span class="stat-label">STRONG MISMATCH</span>
          <span class="stat-value {'warn' if by_lbl.get('STRONG_MISMATCH',0)>0 else 'ok'}">{by_lbl.get('STRONG_MISMATCH',0)}</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">MODERATE MISMATCH</span>
          <span class="stat-value">{by_lbl.get('MODERATE_MISMATCH',0)}</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">WEAK MISMATCH</span>
          <span class="stat-value">{by_lbl.get('WEAK_MISMATCH',0)}</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">ALIGNED</span>
          <span class="stat-value ok">{by_lbl.get('ALIGNED',0)}</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">AVG GAP (all)</span>
          <span class="stat-value">{fmt(avg_gi)}</span>
        </div>
        </div>
        """, unsafe_allow_html=True)



# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TIMELINE
# ═══════════════════════════════════════════════════════════════════════════════
with tab_tl:
    # ── Controls ──────────────────────────────────────────────────────────────
    c1, c2, c3 = st.columns([3, 3, 2], gap="small")
    with c1:
        days_options = {"All time": None, "Last 30 days": 30,
                        "Last 90 days": 90, "Last 7 days": 7}
        days_sel = st.selectbox("Time window", list(days_options.keys()),
                                label_visibility="collapsed")
        days_back = days_options[days_sel]
    with c2:
        tickers_tl = st.multiselect("Tickers (timeline)", tickers_avail,
                                    default=selected_tickers,
                                    label_visibility="collapsed")
    with c3:
        show_ma = st.toggle("7-day moving avg", value=False)

    # ── Filter for this tab ───────────────────────────────────────────────────
    df_tl = df.copy()
    if tickers_tl:
        df_tl = df_tl[df_tl["ticker"].isin(tickers_tl)]
    if days_back:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days_back)
        if df_tl["ts"].dt.tz is None:
            df_tl["ts"] = df_tl["ts"].dt.tz_localize("UTC")
        df_tl = df_tl[df_tl["ts"] >= cutoff]

    # ── Divergence chart ──────────────────────────────────────────────────────
    st.markdown(
        '<div class="section-head" style="margin-top:0.8rem;">'
        'Narrative–Reality Divergence  ·  Gap Index Trend</div>',
        unsafe_allow_html=True)
    st.markdown(
        '<p style="font-family:DM Mono,monospace;font-size:0.67rem;color:#52525b;'
        'margin-bottom:0.6rem;">Amber zone above blue = N ahead of R = divergence signal. '
        'Blue dominant = aligned.</p>',
        unsafe_allow_html=True)

    div_fig = make_divergence_chart(df_tl)

    # Optional: overlay 7-day moving average on Gap Index bars
    if show_ma and not df_tl.empty and "gap_index" in df_tl.columns:
        df_ma = df_tl.sort_values("ts")
        if len(df_ma) >= 3:
            rolling = df_ma["gap_index"].rolling(window=min(7, len(df_ma)),
                                                 min_periods=2).mean()
            div_fig.add_trace(go.Scatter(
                x=df_ma["ts"], y=rolling,
                mode="lines", name="7d MA",
                line=dict(color="#6366f1", width=1.5, dash="dash"),
                hovertemplate="7d MA: %{y:.4f}<extra></extra>",
            ), row=2, col=1)

    st.plotly_chart(div_fig, use_container_width=True)

    # ── Period summary stats ──────────────────────────────────────────────────
    if not df_tl.empty and "gap_index" in df_tl.columns:
        gi_vals = df_tl["gap_index"].dropna()
        if not gi_vals.empty:
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Min Gap",  f"{gi_vals.min():.4f}")
            s2.metric("Max Gap",  f"{gi_vals.max():.4f}")
            s3.metric("Mean Gap", f"{gi_vals.mean():.4f}")
            s4.metric("Records",  str(len(df_tl)))


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════
with tab_an:
    # ── Row 1: evidence quality + label distribution ──────────────────────────
    c_ev, c_lb = st.columns(2, gap="large")

    with c_ev:
        st.markdown('<div class="section-head">Evidence Quality</div>',
                    unsafe_allow_html=True)
        st.markdown(
            '<p style="font-family:DM Mono,monospace;font-size:0.66rem;color:#52525b;">'
            'Distribution of RealityAgent evidence_strength assessments '
            '(post ceiling enforcement).</p>',
            unsafe_allow_html=True)
        st.plotly_chart(make_evidence_donut(df), use_container_width=True)

    with c_lb:
        st.markdown('<div class="section-head">Signal Distribution</div>',
                    unsafe_allow_html=True)
        st.markdown(
            '<p style="font-family:DM Mono,monospace;font-size:0.66rem;color:#52525b;">'
            'Gap Index label counts across all filtered records.</p>',
            unsafe_allow_html=True)
        st.plotly_chart(make_label_bar(df), use_container_width=True)

    # ── Row 2: source tier timeline ───────────────────────────────────────────
    st.markdown('<div class="section-head">Source Tier Over Time</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<p style="font-family:DM Mono,monospace;font-size:0.66rem;color:#52525b;">'
        'T1 = Primary filing (best).  T2 = Expert analysis.  T3 = General media (weakest).  '
        'Dominance of T3 signals the EDGAR/Tier-2 fetcher needs attention.</p>',
        unsafe_allow_html=True)
    st.plotly_chart(make_tier_bar(df), use_container_width=True)

    # ── Row 3: full analysis log ──────────────────────────────────────────────
    st.markdown('<div class="section-head">Analysis Log</div>',
                unsafe_allow_html=True)
    log_rows = df.sort_values("ts", ascending=False).to_dict("records")
    st.markdown(render_table_html(log_rows), unsafe_allow_html=True)

    # ── Row 4: full report ────────────────────────────────────────────────────
    report_md = load_report()
    if report_md:
        with st.expander("Full Logic Hedge Report  (latest run)"):
            st.markdown(report_md)


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="disclaimer">
NRS-1 v3 · Not investment advice · All scores experimental and uncalibrated ·
Gap Index labels are analytical classifications, not trading signals ·
LLM: GLM (Zhipu AI) · Data: SQLite (migrated from nrs1_history.jsonl)
</div>
""", unsafe_allow_html=True)
