"""
NRS-1 Dashboard — Fixed
Fixes:
  1. Analysis Log blank: removed st.dataframe() + overflow:hidden CSS → pure HTML table
  2. Lambda bug: 0.0 values showed "—" — fixed with math.isnan() check
  3. Removed stDataFrame CSS overrides that clipped canvas
"""

import streamlit as st
import json, os, math, datetime
import pandas as pd

st.set_page_config(
    page_title="NRS-1 Workbench",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,300;0,400;0,500;1,400&family=Outfit:wght@300;400;500;600;700&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;1,9..40,300&display=swap');

:root {
  --bg:#0c0c0d; --surface:#111113; --surface2:#161618;
  --border:#232326; --border2:#2a2a2e;
  --text1:#f4f4f5; --text2:#a1a1aa; --text3:#52525b;
  --amber:#f59e0b; --red:#dc2626; --orange:#ea580c;
  --yellow:#ca8a04; --green:#16a34a; --blue:#3b82f6; --indigo:#6366f1;
}

html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"], .main, section.main > div {
    font-family:'DM Sans',system-ui,sans-serif !important;
    background-color:var(--bg) !important;
    color:var(--text1) !important;
}
[data-testid="stSidebar"], [data-testid="stSidebar"] > div {
    background-color:var(--surface) !important;
    border-right:1px solid var(--border) !important;
}
[data-testid="stSidebar"] label, [data-testid="stSidebar"] p, [data-testid="stSidebar"] span {
    color:var(--text2) !important; font-size:0.8rem !important;
}
span[data-baseweb="tag"] {
    background-color:transparent !important;
    border:1px solid var(--border2) !important;
    color:var(--text2) !important;
    border-radius:4px !important;
    font-family:'DM Mono',monospace !important;
    font-size:0.7rem !important;
}
span[data-baseweb="tag"]:hover { border-color:var(--amber) !important; color:var(--amber) !important; }
[data-testid="stAlert"] {
    background-color:var(--surface2) !important;
    border-color:var(--border2) !important;
    font-family:'DM Mono',monospace !important;
    font-size:0.75rem !important;
}
hr { border-color:var(--border) !important; margin:1.5rem 0 !important; }
details { background:var(--surface) !important; border:1px solid var(--border) !important; border-radius:8px !important; }
details summary { color:var(--text2) !important; font-size:0.82rem !important; padding:0.75rem 1rem !important; }
code, pre { font-family:'DM Mono',monospace !important; background:var(--surface2) !important; color:var(--amber) !important; font-size:0.78rem !important; }
#MainMenu, footer, header { visibility:hidden; }
.block-container { padding-top:2rem !important; padding-left:2.5rem !important; padding-right:2.5rem !important; max-width:1400px !important; }

.page-title { font-family:'Outfit',sans-serif; font-size:1.5rem; font-weight:600; color:var(--text1); letter-spacing:-0.02em; border-left:3px solid var(--amber); padding-left:0.9rem; margin-bottom:0.2rem; }
.page-subtitle { font-family:'DM Mono',monospace; font-size:0.68rem; color:var(--text3); letter-spacing:0.12em; padding-left:1.2rem; text-transform:uppercase; }
.gap-number { font-family:'DM Mono',monospace; font-size:4rem; font-weight:500; line-height:1; letter-spacing:-0.04em; }
.gap-number.STRONG_MISMATCH   { color:var(--red); }
.gap-number.MODERATE_MISMATCH { color:var(--orange); }
.gap-number.WEAK_MISMATCH     { color:var(--yellow); }
.gap-number.ALIGNED           { color:var(--green); }
.gap-number.INSUFFICIENT_EVIDENCE { color:var(--text3); }
.label-row { display:flex; align-items:center; gap:0.5rem; margin-top:0.5rem; }
.label-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.label-dot.STRONG_MISMATCH   { background:var(--red); }
.label-dot.MODERATE_MISMATCH { background:var(--orange); }
.label-dot.WEAK_MISMATCH     { background:var(--yellow); }
.label-dot.ALIGNED           { background:var(--green); }
.label-name { font-family:'DM Mono',monospace; font-size:0.72rem; font-weight:500; letter-spacing:0.06em; color:var(--text1); }
.label-desc { font-size:0.78rem; color:var(--text2); margin-top:0.35rem; line-height:1.5; max-width:340px; }
.score-grid { display:grid; gap:0.55rem; margin:1.2rem 0; }
.score-item { display:grid; grid-template-columns:80px 1fr 52px; align-items:center; gap:0.6rem; }
.score-key { font-family:'DM Mono',monospace; font-size:0.67rem; color:var(--text3); text-align:right; }
.score-track { height:4px; background:var(--border2); border-radius:2px; overflow:hidden; }
.score-fill { height:100%; border-radius:2px; background:var(--text3); }
.score-fill.is-gap { background:var(--red); }
.score-fill.is-narrative { background:var(--amber); }
.score-fill.is-reality { background:var(--blue); }
.score-fill.is-market { background:var(--indigo); }
.score-val { font-family:'DM Mono',monospace; font-size:0.67rem; color:var(--text2); text-align:right; }
.claim-card { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:1.1rem 1.25rem; }
.claim-ticker { font-family:'DM Mono',monospace; font-size:0.7rem; color:var(--amber); letter-spacing:0.1em; text-transform:uppercase; margin-bottom:0.5rem; }
.claim-text { font-size:0.88rem; color:var(--text1); line-height:1.6; margin-bottom:0.75rem; }
.claim-meta { font-family:'DM Mono',monospace; font-size:0.65rem; color:var(--text3); }
.stat-row { display:flex; justify-content:space-between; padding:0.55rem 0; border-bottom:1px solid var(--border); }
.stat-row:last-child { border-bottom:none; }
.stat-label { font-family:'DM Mono',monospace; font-size:0.7rem; color:var(--text3); letter-spacing:0.06em; }
.stat-value { font-family:'DM Mono',monospace; font-size:0.7rem; color:var(--text1); font-weight:500; }
.stat-value.warn { color:var(--red); }
.stat-value.caution { color:var(--orange); }
.stat-value.ok { color:var(--green); }
.section-head { font-family:'DM Mono',monospace; font-size:0.65rem; font-weight:500; letter-spacing:0.15em; color:var(--text3); text-transform:uppercase; padding-bottom:0.5rem; border-bottom:1px solid var(--border); margin-bottom:1rem; }
.disclaimer { font-family:'DM Mono',monospace; font-size:0.65rem; color:var(--text3); border-top:1px solid var(--border); padding-top:1rem; line-height:1.7; }
</style>
""", unsafe_allow_html=True)


# ── CONSTANTS ─────────────────────────────────────────────────────────────────
HISTORY_PATH = "nrs1_history.jsonl"
REPORT_PATH  = "nrs1_report.md"
AUDIT_PATH   = "nrs1_audit.jsonl"

DOT_COLOR = {
    "STRONG_MISMATCH":"#dc2626", "MODERATE_MISMATCH":"#ea580c",
    "WEAK_MISMATCH":"#ca8a04",  "ALIGNED":"#16a34a",
    "INSUFFICIENT_EVIDENCE":"#52525b",
}
LABEL_DESC = {
    "STRONG_MISMATCH":       "Narrative far ahead of engineering reality.",
    "MODERATE_MISMATCH":     "Material divergence between narrative and reality.",
    "WEAK_MISMATCH":         "Minor gap. Within uncertainty range.",
    "ALIGNED":               "Narrative and engineering roughly consistent.",
    "INSUFFICIENT_EVIDENCE": "Insufficient evidence to score.",
}


# ── SAFE FORMATTER (fixes 0.0 bug) ────────────────────────────────────────────
def fmt(x, d=4):
    """Format float safely — handles None, NaN, and 0.0 correctly."""
    if x is None: return "—"
    try:
        f = float(x)
        return "—" if math.isnan(f) else f"{f:.{d}f}"
    except:
        return "—"


# ── DATA ─────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_history():
    if not os.path.exists(HISTORY_PATH): return pd.DataFrame()
    rows = []
    with open(HISTORY_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try: rows.append(json.loads(line))
                except: pass
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df.sort_values("ts")

@st.cache_data(ttl=60)
def load_report():
    if not os.path.exists(REPORT_PATH): return ""
    return open(REPORT_PATH).read()

@st.cache_data(ttl=60)
def audit_count():
    if not os.path.exists(AUDIT_PATH): return 0
    return sum(1 for _ in open(AUDIT_PATH))


# ── HTML TABLE (replaces st.dataframe — no canvas, no CSS conflicts) ──────────
def render_table_html(df_rows: list[dict]) -> str:
    """
    Pure HTML table. Fully styled with inline CSS.
    No dependency on st.dataframe or glide-data-grid canvas.
    """
    TD = "padding:7px 10px;font-size:0.72rem;color:#a1a1aa;border-bottom:1px solid #1a1a1c;font-family:'DM Mono',monospace;white-space:nowrap;"

    headers = ["Date","Tkr","Gap","Label","N","R","M","Evid","Claim"]
    thead   = "".join(
        f'<th style="text-align:left;padding:6px 10px;color:#3f3f46;font-size:0.65rem;'
        f'letter-spacing:.1em;border-bottom:1px solid #232326;font-weight:500;'
        f'white-space:nowrap;background:#161618;">{h}</th>'
        for h in headers
    )

    tbody = ""
    for i, r in enumerate(df_rows):
        lbl   = r.get("gap_label", "")
        dc    = DOT_COLOR.get(lbl, "#52525b")
        short = lbl.replace("_MISMATCH", "").replace("_", " ")
        bg    = "rgba(255,255,255,0.012)" if i % 2 == 0 else "transparent"
        claim_text = str(r.get("claim", ""))
        claim_disp = claim_text[:55] + ("…" if len(claim_text) > 55 else "")

        ts_val = r.get("ts", "")
        if hasattr(ts_val, "strftime"):
            ts_val = ts_val.strftime("%m-%d %H:%M")

        cells = "".join([
            f'<td style="{TD}">{ts_val}</td>',
            f'<td style="{TD};color:#f59e0b;font-weight:500;">{r.get("ticker","—")}</td>',
            f'<td style="{TD};color:{dc};font-weight:600;">{fmt(r.get("gap_index"))}</td>',
            f'<td style="{TD}"><span style="display:inline-flex;align-items:center;gap:5px;">'
            f'<span style="width:5px;height:5px;border-radius:50%;background:{dc};flex-shrink:0;"></span>'
            f'<span style="color:{dc};font-size:0.65rem;">{short}</span></span></td>',
            f'<td style="{TD}">{fmt(r.get("n_score"), 3)}</td>',
            f'<td style="{TD}">{fmt(r.get("r_score"), 3)}</td>',
            f'<td style="{TD}">{fmt(r.get("m_implied"), 3)}</td>',
            f'<td style="{TD}">{r.get("evidence","—")}</td>',
            f'<td style="{TD};color:#52525b;max-width:220px;overflow:hidden;'
            f'text-overflow:ellipsis;">{claim_disp}</td>',
        ])
        tbody += f'<tr style="background:{bg};">{cells}</tr>'

    return (
        f'<div style="overflow-x:auto;border:1px solid #232326;border-radius:8px;'
        f'background:#111113;max-height:320px;overflow-y:auto;">'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'<thead><tr>{thead}</tr></thead>'
        f'<tbody>{tbody}</tbody>'
        f'</table></div>'
    )


# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="section-head">Engine Parameters</div>', unsafe_allow_html=True)
    alpha = st.slider("α  NR weight", 0.0, 1.0, 0.5, 0.05)
    beta  = st.slider("β  MR weight", 0.0, 1.0, 0.5, 0.05)
    ok    = abs(alpha + beta - 1.0) < 1e-9
    color_check = "#16a34a" if ok else "#dc2626"
    st.markdown(f'<p style="font-family:DM Mono,monospace;font-size:0.72rem;color:{color_check};margin-top:.3rem;">α + β = {alpha+beta:.2f}  {"✓" if ok else "✗"}</p>', unsafe_allow_html=True)

    st.markdown('<hr/>', unsafe_allow_html=True)

    df_all       = load_history()
    tickers_avail = sorted(df_all["ticker"].unique().tolist()) if not df_all.empty and "ticker" in df_all.columns else []
    st.markdown('<div class="section-head">Filter</div>', unsafe_allow_html=True)
    selected = st.multiselect("Tickers", tickers_avail, default=tickers_avail, label_visibility="collapsed")

    st.markdown('<hr/>', unsafe_allow_html=True)
    ac = audit_count()
    st.markdown(f"""
    <div class="stat-row"><span class="stat-label">AUDIT ENTRIES</span><span class="stat-value">{ac}</span></div>
    <div class="stat-row"><span class="stat-label">HISTORY RECORDS</span><span class="stat-value">{len(df_all)}</span></div>
    <div class="stat-row"><span class="stat-label">VERSION</span><span class="stat-value">NRS-1 v2</span></div>
    """, unsafe_allow_html=True)
    st.markdown('<p style="font-family:DM Mono,monospace;font-size:0.62rem;color:#3f3f46;margin-top:1.5rem;">Not investment advice.</p>', unsafe_allow_html=True)


# ── HEADER ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="page-title">NRS-1 Mispricing Workbench</div>
<div class="page-subtitle">Narrative · Reality · Market — Gap Index</div>
<hr/>
""", unsafe_allow_html=True)


# ── LOAD + FILTER ──────────────────────────────────────────────────────────────
df = load_history()
if not df.empty and selected:
    df = df[df["ticker"].isin(selected)]

if df.empty:
    st.markdown('<p style="color:#71717a;font-family:DM Mono,monospace;font-size:0.8rem;">No data. Run: <code>python nrs1_v2.py --stub</code></p>', unsafe_allow_html=True)
    st.stop()


# ── LATEST ANALYSIS ───────────────────────────────────────────────────────────
latest = df.iloc[-1]
label  = str(latest.get("gap_label", "UNKNOWN"))
gi     = float(latest.get("gap_index") or 0)

col1, col2 = st.columns([5, 3], gap="large")

with col1:
    st.markdown('<div class="section-head">Latest Reading</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="gap-number {label}">{gi:.4f}</div>
    <div class="label-row">
      <div class="label-dot {label}"></div>
      <span class="label-name">{label.replace("_"," ")}</span>
    </div>
    <p class="label-desc">{LABEL_DESC.get(label,"")}</p>
    """, unsafe_allow_html=True)

    scores = [
        ("N_score",  latest.get("n_score",  0), "is-narrative"),
        ("R_score",  latest.get("r_score",  0), "is-reality"),
        ("M_implied",latest.get("m_implied",0), "is-market"),
        ("NR_gap",   latest.get("nr_gap",   0), "is-gap"),
        ("MR_gap",   latest.get("mr_gap",   0), "is-gap"),
    ]
    bars_html = '<div class="score-grid">'
    for key, val, css_class in scores:
        v   = float(val) if (val is not None and not (isinstance(val, float) and math.isnan(val))) else 0.0
        pct = max(0, min(100, v * 100))
        bars_html += f'<div class="score-item"><div class="score-key">{key}</div><div class="score-track"><div class="score-fill {css_class}" style="width:{pct}%"></div></div><div class="score-val">{v:.4f}</div></div>'
    bars_html += '</div>'
    st.markdown(bars_html, unsafe_allow_html=True)

with col2:
    st.markdown('<div class="section-head">Latest Claim</div>', unsafe_allow_html=True)
    ticker  = latest.get("ticker", "—")
    claim   = latest.get("claim", "—")
    ts_raw  = latest.get("ts")
    ts_str  = ts_raw.strftime("%Y-%m-%d %H:%M UTC") if hasattr(ts_raw, "strftime") else str(ts_raw)[:16]
    evidence= latest.get("evidence", "—")
    mode    = latest.get("mode", "—")

    st.markdown(f"""
    <div class="claim-card">
      <div class="claim-ticker">{ticker}</div>
      <div class="claim-text">{claim}</div>
      <div class="claim-meta">{ts_str} · evidence: {evidence} · {mode}</div>
    </div>
    """, unsafe_allow_html=True)

    strong_n = len(df[df["gap_label"] == "STRONG_MISMATCH"])
    mod_n    = len(df[df["gap_label"] == "MODERATE_MISMATCH"])
    weak_n   = len(df[df["gap_label"] == "WEAK_MISMATCH"])
    aligned_n= len(df[df["gap_label"] == "ALIGNED"])
    avg_gi   = df["gap_index"].mean()

    st.markdown(f"""
    <div style="margin-top:1.25rem;">
    <div class="stat-row"><span class="stat-label">STRONG MISMATCH</span><span class="stat-value {'warn' if strong_n>0 else 'ok'}">{strong_n}</span></div>
    <div class="stat-row"><span class="stat-label">MODERATE</span><span class="stat-value {'caution' if mod_n>3 else ''}">{mod_n}</span></div>
    <div class="stat-row"><span class="stat-label">WEAK</span><span class="stat-value">{weak_n}</span></div>
    <div class="stat-row"><span class="stat-label">ALIGNED</span><span class="stat-value ok">{aligned_n}</span></div>
    <div class="stat-row"><span class="stat-label">AVG GAP (14d)</span><span class="stat-value">{avg_gi:.4f}</span></div>
    </div>
    """, unsafe_allow_html=True)


st.markdown("<hr/>", unsafe_allow_html=True)


# ── CHART ─────────────────────────────────────────────────────────────────────
st.markdown('<div class="section-head">Gap Index — Historical Trend</div>', unsafe_allow_html=True)

try:
    import plotly.graph_objects as go

    fig = go.Figure()
    zones = [(0.60,1.05,"rgba(220,38,38,0.04)"),(0.35,0.60,"rgba(234,88,12,0.03)"),(0.15,0.35,"rgba(202,138,4,0.03)")]
    for y0,y1,fc in zones:
        fig.add_hrect(y0=y0,y1=y1,fillcolor=fc,line_width=0)
    for y,col,lbl in [(0.60,"#dc2626","Strong"),(0.35,"#ea580c","Mod."),(0.15,"#ca8a04","Weak")]:
        fig.add_hline(y=y, line=dict(color=col,width=0.5,dash="dot"),
                      annotation_text=lbl, annotation_font=dict(size=9,color=col),
                      annotation_position="right")
    if "nr_gap" in df.columns:
        fig.add_trace(go.Scatter(x=df["ts"],y=df["nr_gap"],mode="lines",name="NR gap",
            line=dict(color="rgba(234,88,12,0.35)",width=1,dash="dot"),
            hovertemplate="NR: %{y:.4f}<extra></extra>"))
    if "mr_gap" in df.columns:
        fig.add_trace(go.Scatter(x=df["ts"],y=df["mr_gap"],mode="lines",name="MR gap",
            line=dict(color="rgba(99,102,241,0.35)",width=1,dash="dot"),
            hovertemplate="MR: %{y:.4f}<extra></extra>"))
    mc = [DOT_COLOR.get(l,"#f59e0b") for l in df["gap_label"]]
    fig.add_trace(go.Scatter(x=df["ts"],y=df["gap_index"],mode="lines+markers",name="Gap Index",
        line=dict(color="#f59e0b",width=1.5),
        marker=dict(size=6,color=mc,line=dict(width=1,color="#0c0c0d")),
        hovertemplate="<b>%{x|%b %d}</b>  %{y:.4f}<extra></extra>"))
    fig.update_layout(
        plot_bgcolor="#0c0c0d", paper_bgcolor="#0c0c0d",
        font=dict(family="DM Mono",size=10,color="#52525b"),
        xaxis=dict(gridcolor="#161618",showgrid=True,zeroline=False,tickfont=dict(size=9),showline=False),
        yaxis=dict(gridcolor="#161618",showgrid=True,zeroline=False,range=[-0.02,1.05],tickformat=".2f",tickfont=dict(size=9)),
        legend=dict(bgcolor="rgba(0,0,0,0)",font=dict(size=9,color="#52525b"),orientation="h",y=-0.15),
        margin=dict(l=0,r=60,t=10,b=20), height=280, hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)
except ImportError:
    st.line_chart(df[["ts","gap_index"]].set_index("ts"))


st.markdown("<hr/>", unsafe_allow_html=True)


# ── ANALYSIS LOG — pure HTML table (fixes blank rendering) ────────────────────
st.markdown('<div class="section-head">Analysis Log</div>', unsafe_allow_html=True)

table_rows = df.sort_values("ts", ascending=False).to_dict("records")
st.markdown(render_table_html(table_rows), unsafe_allow_html=True)


# ── REPORT ────────────────────────────────────────────────────────────────────
report = load_report()
if report:
    with st.expander("Full Logic Hedge Report"):
        st.markdown(report)


# ── FOOTER ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="disclaimer">
NRS-1 v2 · Narrative-Reality Mispricing Workbench · Not investment advice ·
All scores experimental and uncalibrated · Gap Index labels are analytical
classifications, not trading signals.
</div>
""", unsafe_allow_html=True)
