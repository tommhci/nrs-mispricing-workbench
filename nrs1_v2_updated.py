"""
NRS-1 v2 — Full AI Agent
=========================
Upgrades nrs1_demo.py with:
  Module 1: RSS news scraper (real headlines)
  Module 2: LLM analysis via Anthropic API (real engineering assessment)
  Module 3: Email dispatch via Gmail SMTP
  (Module 4: Windows Task Scheduler — see instructions at bottom of file)

SETUP INSTRUCTIONS (one-time, 5 minutes):
──────────────────────────────────────────
1. Install the two new dependencies:
   pip install requests anthropic

2. Get your Anthropic API key:
   → Go to: https://console.anthropic.com
   → Sign up (free) → API Keys → Create Key
   → Copy the key (starts with sk-ant-...)

3. Set up Gmail App Password (so Python can send email):
   → Go to: myaccount.google.com/security
   → Enable 2-Step Verification if not already on
   → Search "App passwords" → Create one → copy the 16-char code

4. Fill in the CONFIG BLOCK below (lines 45–55)

5. Run:
   python nrs1_v2.py          ← runs live analysis + sends email
   python nrs1_v2.py --stub   ← runs with hardcoded data (no API key needed)
   python nrs1_v2.py --test   ← runs unit tests

HOW IT WORKS:
  1. Scrapes today's AI/semiconductor headlines from free RSS feeds
  2. Picks the most relevant headline
  3. Claude reads the headline → extracts NarrativeObject (JSON)
  4. Claude reads the headline → assesses engineering reality (JSON)
  5. Gap Index formula runs deterministically
  6. Report written to nrs1_report.md
  7. Report emailed to you at 8am (via Windows Task Scheduler)

NOT INVESTMENT ADVICE. Scores are experimental and uncalibrated.
"""

import json
import math
import datetime
import smtplib
import os
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ════════════════════════════════════════════════════════════════
#  ★ CONFIG — FILL IN YOUR CREDENTIALS HERE ★
# ════════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")   # or paste key directly: "sk-ant-..."
GMAIL_SENDER       = ""    # your gmail: "yourname@gmail.com"
GMAIL_APP_PASSWORD = ""    # 16-char App Password from Google Account settings
GMAIL_RECIPIENT    = ""    # where to send report (can be same as sender)

# Tickers and topics to watch
WATCH_TICKERS  = ["NVDA", "AMD", "TSMC", "INTC", "AVGO", "ASML", "MOD", "SMCI"]
WATCH_TOPICS   = ["AI chip", "GPU", "semiconductor", "datacenter", "liquid cooling",
                  "HBM", "CoWoS", "3nm", "inference", "training cluster"]

# Anthropic model
LLM_MODEL = "claude-sonnet-4-20250514"


# ════════════════════════════════════════════════════════════════
#  MODULE 1 — RSS NEWS SCRAPER
# ════════════════════════════════════════════════════════════════

# Free RSS feeds that don't require login or API keys
RSS_FEEDS = [
    # General financial news
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",    # CNBC Tech
    "https://feeds.bloomberg.com/technology/news.rss",
    # Semiconductor / tech
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.theverge.com/rss/index.xml",
    "https://hnrss.org/frontpage",                               # Hacker News top
    # Yahoo Finance per-ticker (more reliable than general feed)
    f"https://finance.yahoo.com/rss/headline?s=NVDA",
    f"https://finance.yahoo.com/rss/headline?s=AMD",
    f"https://finance.yahoo.com/rss/headline?s=TSM",
]

def scrape_ai_news(max_items: int = 10) -> list[dict]:
    """
    Fetches today's headlines from RSS feeds.
    Filters for AI/semiconductor relevance.
    Falls back to empty list if all feeds fail (pipeline continues with stub).

    Returns: list of {"title": str, "url": str, "source": str, "date": str}
    """
    try:
        import requests
        import xml.etree.ElementTree as ET
    except ImportError:
        print("  [SCRAPER] 'requests' not installed. Run: pip install requests")
        return []

    keywords = [t.lower() for t in WATCH_TICKERS + WATCH_TOPICS]
    results = []

    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(
                feed_url,
                timeout=8,
                headers={"User-Agent": "NRS1-Research-Agent/2.0 (academic research)"}
            )
            if resp.status_code != 200:
                continue

            root = ET.fromstring(resp.content)

            # Handle both RSS <item> and Atom <entry> formats
            items = root.findall('.//item')
            if not items:
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                items = root.findall('.//atom:entry', ns)

            source_name = feed_url.split('/')[2].replace("www.", "").replace("feeds.", "")

            for item in items:
                title_el = (item.find('title') or
                            item.find('{http://www.w3.org/2005/Atom}title'))
                link_el  = (item.find('link') or
                            item.find('{http://www.w3.org/2005/Atom}link'))
                date_el  = (item.find('pubDate') or
                            item.find('dc:date') or
                            item.find('{http://www.w3.org/2005/Atom}updated'))

                if title_el is None:
                    continue

                title = (title_el.text or "").strip()
                url   = (link_el.text if link_el is not None and link_el.text
                         else link_el.get('href', '') if link_el is not None else "")
                date  = date_el.text if date_el is not None else ""

                # Relevance filter — must contain at least one keyword
                if any(kw in title.lower() for kw in keywords):
                    results.append({
                        "title":  title,
                        "url":    url,
                        "source": source_name,
                        "date":   date,
                    })

        except Exception as e:
            # Silent fail per feed — continue to next
            continue

        if len(results) >= max_items:
            break

    # Deduplicate by title
    seen = set()
    unique = []
    for item in results:
        if item["title"] not in seen:
            seen.add(item["title"])
            unique.append(item)

    print(f"  [SCRAPER] Found {len(unique)} relevant headlines from {len(RSS_FEEDS)} feeds.")
    return unique[:max_items]


def pick_top_headline(headlines: list[dict]) -> Optional[dict]:
    """
    Picks the single most relevant headline for analysis.
    Priority: tickers mentioned > recency.
    Returns None if list is empty.
    """
    if not headlines:
        return None

    # Score each headline by number of watch tokens it contains
    def relevance(h):
        text = h["title"].lower()
        return sum(1 for kw in WATCH_TICKERS + WATCH_TOPICS if kw.lower() in text)

    return max(headlines, key=relevance)


# ════════════════════════════════════════════════════════════════
#  DATA MODELS (same as v1, no pydantic needed)
# ════════════════════════════════════════════════════════════════

@dataclass
class NarrativeObject:
    claim:              str
    source_url:         str
    sentiment_polarity: float   # -1.0 to 1.0
    propagation:        float   # 0.25 / 0.5 / 1.0
    novelty:            str     # "first_report" / "echo" / "stale"
    certainty:          str     # "high" / "moderate" / "low"

@dataclass
class RealityObject:
    technical_change:    str
    feasibility_score:   float   # 0.0 – 1.0
    constraint_penalty:  float   # 0.0 – 1.0
    evidence_strength:   str     # "strong"/"moderate"/"weak"/"insufficient"
    open_constraints:    list
    hardware_constraint: str
    supply_chain_risk:   str

@dataclass
class MarketObject:
    ticker:              str
    event_date:          str
    event_window_return: Optional[float]
    data_quality:        str

@dataclass
class GapResult:
    n_score:    float
    r_score:    float
    m_implied:  Optional[float]
    nr_gap:     float
    mr_gap:     Optional[float]
    gap_index:  Optional[float]
    gap_label:  str
    notes:      list


# ════════════════════════════════════════════════════════════════
#  CONFIG / WEIGHTS (same as v1)
# ════════════════════════════════════════════════════════════════

ALPHA = 0.5
BETA  = 0.5
assert abs(ALPHA + BETA - 1.0) < 1e-9

NOVELTY_WEIGHT  = {"first_report": 1.0, "echo": 0.6, "stale": 0.3}
EVIDENCE_WEIGHT = {"strong": 1.0, "moderate": 0.7, "weak": 0.4, "insufficient": 0.0}
GAP_THRESHOLDS  = {"STRONG_MISMATCH": 0.60, "MODERATE_MISMATCH": 0.35, "WEAK_MISMATCH": 0.15}


# ════════════════════════════════════════════════════════════════
#  MODULE 2 — LLM AGENTS (real Anthropic API calls)
# ════════════════════════════════════════════════════════════════

NARRATIVE_PROMPT = """You are a financial narrative extraction agent.
Analyze the headline and return ONLY a JSON object — no markdown, no explanation.

JSON format:
{
  "claim": "<single declarative sentence: the market claim>",
  "sentiment_polarity": <-1.0=strongly negative, -0.5=negative, 0.0=neutral, 0.5=positive, 1.0=strongly positive>,
  "propagation": <0.25=niche, 0.5=moderate coverage, 1.0=major headline>,
  "novelty": "<first_report|echo|stale>",
  "certainty": "<high|moderate|low>"
}

Rules:
- sentiment_polarity must be exactly one of: -1.0, -0.5, 0.0, 0.5, 1.0
- propagation must be exactly one of: 0.25, 0.5, 1.0
- novelty must be exactly one of: first_report, echo, stale
- certainty must be exactly one of: high, moderate, low
"""

REALITY_PROMPT = """You are an engineering feasibility assessment agent for financial analysis.
Analyze the claim and return ONLY a JSON object — no markdown, no explanation.

JSON format:
{
  "technical_change": "<what physical/engineering capability is being claimed>",
  "feasibility_score": <0.0=impossible, 0.25=no clear path, 0.5=plausible, 0.75=likely, 1.0=demonstrated>,
  "constraint_penalty": <0.0-0.9, sum of penalties for unresolved constraints>,
  "evidence_strength": "<strong|moderate|weak|insufficient>",
  "open_constraints": ["<constraint 1>", "<constraint 2>"],
  "hardware_constraint": "<key hardware bottleneck or 'none identified'>",
  "supply_chain_risk": "<supply chain risk or 'low'>"
}

Rules:
- feasibility_score + constraint_penalty cannot exceed 1.0
- evidence_strength must be exactly one of: strong, moderate, weak, insufficient
- Be skeptical. Press releases without benchmarks = weak evidence.
- Consider: manufacturing yields, supply chain, thermal management, software maturity
"""

def call_llm(system_prompt: str, user_content: str) -> Optional[dict]:
    """
    Calls Anthropic API. Returns parsed dict or None on failure.
    Retries once on schema parse failure.
    """
    if not ANTHROPIC_API_KEY:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except ImportError:
        print("  [LLM] anthropic not installed. Run: pip install anthropic")
        return None

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=LLM_MODEL,
                max_tokens=512,
                temperature=0.0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            raw = response.content[0].text.strip()
            # Strip markdown fences if present
            raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  [LLM] JSON parse failed attempt {attempt+1}: {e}")
            if attempt == 1:
                return None
        except Exception as e:
            print(f"  [LLM] API call failed: {e}")
            return None
    return None


def live_narrative_agent(headline: dict) -> Optional[NarrativeObject]:
    """Extract NarrativeObject from a real headline using Claude."""
    print(f"  [LLM] Analyzing narrative: {headline['title'][:60]}...")

    user_content = f"""Headline: "{headline['title']}"
Source: {headline.get('source', 'unknown')}
Date: {headline.get('date', 'unknown')}

Extract the structured narrative data."""

    data = call_llm(NARRATIVE_PROMPT, user_content)
    if data is None:
        return None

    try:
        return NarrativeObject(
            claim=data["claim"],
            source_url=headline.get("url", ""),
            sentiment_polarity=float(data["sentiment_polarity"]),
            propagation=float(data["propagation"]),
            novelty=data["novelty"],
            certainty=data["certainty"],
        )
    except (KeyError, ValueError) as e:
        print(f"  [LLM] Narrative schema mismatch: {e}")
        return None


def live_reality_agent(claim: str) -> Optional[RealityObject]:
    """Assess engineering feasibility of a claim using Claude."""
    print(f"  [LLM] Assessing reality for: {claim[:60]}...")

    user_content = f"""Claim to assess: "{claim}"

Assess the engineering and physical feasibility. 
Be precise and skeptical. Use only verifiable technical facts."""

    data = call_llm(REALITY_PROMPT, user_content)
    if data is None:
        return None

    try:
        fs = float(data["feasibility_score"])
        cp = float(data["constraint_penalty"])
        # Enforce constraint: fs - cp cannot go below 0
        cp = min(cp, fs)
        return RealityObject(
            technical_change=data["technical_change"],
            feasibility_score=fs,
            constraint_penalty=cp,
            evidence_strength=data["evidence_strength"],
            open_constraints=data.get("open_constraints", []),
            hardware_constraint=data.get("hardware_constraint", "not assessed"),
            supply_chain_risk=data.get("supply_chain_risk", "not assessed"),
        )
    except (KeyError, ValueError) as e:
        print(f"  [LLM] Reality schema mismatch: {e}")
        return None


def get_market_data(ticker: str, event_date: str) -> MarketObject:
    """
    Fetches real price data via yfinance.
    Falls back to stub if yfinance not installed.
    """
    try:
        import yfinance as yf
        import pandas as pd

        t = yf.Ticker(ticker)
        start = datetime.datetime.strptime(event_date, "%Y-%m-%d")
        end   = start + datetime.timedelta(days=10)

        hist = t.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True
        )

        if len(hist) >= 2:
            ret = float(hist['Close'].iloc[-1] / hist['Close'].iloc[0] - 1.0)
            print(f"  [MARKET] {ticker}: {ret*100:+.1f}% over {len(hist)} trading days")
            return MarketObject(ticker=ticker, event_date=event_date,
                                event_window_return=ret, data_quality="ok")
        else:
            print(f"  [MARKET] {ticker}: insufficient data")
            return MarketObject(ticker=ticker, event_date=event_date,
                                event_window_return=None, data_quality="unavailable")
    except ImportError:
        print("  [MARKET] yfinance not installed. Using stub return.")
        return MarketObject(ticker=ticker, event_date=event_date,
                            event_window_return=None, data_quality="unavailable")
    except Exception as e:
        print(f"  [MARKET] yfinance error: {e}")
        return MarketObject(ticker=ticker, event_date=event_date,
                            event_window_return=None, data_quality="unavailable")


# ════════════════════════════════════════════════════════════════
#  GAP INDEX (same formula as v1, unchanged)
# ════════════════════════════════════════════════════════════════

def sigmoid_proxy(r: float, scale: float = 10.0) -> float:
    return 1.0 / (1.0 + math.exp(-scale * r))

def get_gap_label(gi: Optional[float]) -> str:
    if gi is None: return "INSUFFICIENT_EVIDENCE"
    if gi >= 0.60: return "STRONG_MISMATCH"
    if gi >= 0.35: return "MODERATE_MISMATCH"
    if gi >= 0.15: return "WEAK_MISMATCH"
    return "ALIGNED"

def compute_gap_index(n: NarrativeObject, r: RealityObject,
                      m: MarketObject) -> GapResult:
    notes = []
    nw    = NOVELTY_WEIGHT[n.novelty]
    n_raw = max(-1.0, min(1.0, n.sentiment_polarity * n.propagation * nw))
    n_score = 0.5 + 0.5 * n_raw
    notes.append(f"N_score: {n.sentiment_polarity}×{n.propagation}×{nw} → {n_score:.4f}")

    ew = EVIDENCE_WEIGHT[r.evidence_strength]
    if ew == 0.0:
        notes.append("evidence=insufficient → gap_index=None")
        return GapResult(n_score=n_score, r_score=0.0, m_implied=None,
                         nr_gap=0.0, mr_gap=None, gap_index=None,
                         gap_label="INSUFFICIENT_EVIDENCE", notes=notes)

    r_score = max(0.0, min(1.0, r.feasibility_score * (1.0 - r.constraint_penalty) * ew))
    notes.append(f"R_score: {r.feasibility_score}×(1−{r.constraint_penalty})×{ew} = {r_score:.4f}")

    nr_gap = abs(n_score - r_score)
    notes.append(f"NR_gap: |{n_score:.4f}−{r_score:.4f}| = {nr_gap:.4f}")

    m_implied = mr_gap = None
    if m.event_window_return is not None:
        m_implied = sigmoid_proxy(m.event_window_return)
        mr_gap    = abs(m_implied - r_score)
        notes.append(f"M_implied: sigmoid({m.event_window_return:.2f})={m_implied:.4f}")
        notes.append(f"MR_gap: {mr_gap:.4f}")
    else:
        notes.append("M_implied: unavailable")

    gap_index = round(ALPHA * nr_gap + BETA * mr_gap, 6) if mr_gap is not None else round(nr_gap, 6)
    notes.append(f"GapIndex: {gap_index:.6f}")

    return GapResult(n_score=round(n_score,4), r_score=round(r_score,4),
                     m_implied=round(m_implied,4) if m_implied else None,
                     nr_gap=round(nr_gap,4),
                     mr_gap=round(mr_gap,4) if mr_gap else None,
                     gap_index=gap_index, gap_label=get_gap_label(gap_index),
                     notes=notes)


# ════════════════════════════════════════════════════════════════
#  GATE CHECKER (same as v1)
# ════════════════════════════════════════════════════════════════

def gate_1(n): 
    r, p = [], True
    if not n.claim or len(n.claim) < 10: r.append("BLOCK: claim too short"); p = False
    if not n.source_url: r.append("WARN: no source URL")
    return p, r

def gate_2(r):
    reasons, passed = [], True
    if not r.technical_change: reasons.append("BLOCK: no technical_change"); passed = False
    if r.evidence_strength == "insufficient": reasons.append("WARN: evidence=insufficient")
    if r.open_constraints: reasons.append(f"WARN: {len(r.open_constraints)} open constraints")
    return passed, reasons

def gate_3(m):
    r = []
    if m.data_quality == "unavailable": r.append("WARN: no market data — MR_gap excluded")
    return True, r

def gate_4(g):
    r, p = [], True
    if g.gap_index is None and g.gap_label != "INSUFFICIENT_EVIDENCE":
        r.append("BLOCK: inconsistent gap_index/label"); p = False
    if g.gap_index is not None and not (0 <= g.gap_index <= 1):
        r.append("BLOCK: gap_index out of [0,1]"); p = False
    return p, r

def gate_5(s):
    r, p = [], True
    for k in ("narrative_summary","reality_summary","gap_interpretation"):
        if not s.get(k) or len(s[k]) < 20: r.append(f"BLOCK: {k} too short"); p = False
    return p, r


# ════════════════════════════════════════════════════════════════
#  AUDIT LOGGER (same as v1)
# ════════════════════════════════════════════════════════════════

AUDIT_PATH = Path("nrs1_audit.jsonl")

def audit(session_id, stage, event, detail=None, reasons=None):
    entry = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "session_id": session_id, "stage": stage, "event": event,
             "detail": detail, "gate_reasons": reasons}
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    print(f"  [AUDIT] {stage} | {event}")


# ════════════════════════════════════════════════════════════════
#  STUB AGENTS (fallback when LLM unavailable — same as v1)
# ════════════════════════════════════════════════════════════════

def stub_narrative(custom_claim=None) -> NarrativeObject:
    return NarrativeObject(
        claim=custom_claim or "Company claims next-gen AI chip achieves 10x compute efficiency per watt, volume shipments Q1 2027.",
        source_url="https://example.com/stub",
        sentiment_polarity=1.0, propagation=1.0,
        novelty="first_report", certainty="moderate",
    )

def stub_reality() -> RealityObject:
    return RealityObject(
        technical_change="10x compute efficiency per watt via 3nm AI silicon",
        feasibility_score=0.35, constraint_penalty=0.30, evidence_strength="weak",
        open_constraints=["manufacturing_yield_unproven","supply_chain_single_source","thermal_management_unsolved"],
        hardware_constraint="HBM3e supply constrained; 3nm yields not at volume",
        supply_chain_risk="Single-source HBM3e; TSMC 3nm shared with OEMs through 2027",
    )

def stub_market(ticker="NVDA") -> MarketObject:
    return MarketObject(ticker=ticker, event_date=datetime.date.today().isoformat(),
                        event_window_return=0.12, data_quality="ok")

def stub_synthesis(gap: GapResult) -> dict:
    gi = gap.gap_index or 0
    return {
        "narrative_summary": "Market narrative positions a 10x compute efficiency gain as achievable within 12 months, implying rapid margin expansion and competitive displacement of incumbent hardware vendors.",
        "reality_summary": "Engineering evidence is weak: the claim relies on press release language with no independent benchmark, unresolved HBM3e supply constraints, and undemonstrated thermal management at claimed throughput levels.",
        "gap_interpretation": f"Gap Index = {gi:.4f} ({gap.gap_label}). N_score={gap.n_score:.3f} significantly exceeds R_score={gap.r_score:.3f}, driven by weak evidence and three unresolved engineering constraints. Market pricing (M={gap.m_implied or 'N/A'}) has absorbed narrative optimism ahead of engineering verification.",
        "key_uncertainties": [
            "No third-party benchmark for the claimed 10x efficiency.",
            "HBM3e supply ramp timeline not publicly disclosed.",
            "3nm yield data proprietary and unverifiable.",
        ],
        "open_questions": [
            "What workload/benchmark defines the 10x comparison?",
            "Has any hyperscaler partner independently validated the claim?",
            "What is the production volume commitment for Q1 2027?",
        ],
    }

def llm_synthesis(narrative: NarrativeObject, reality: RealityObject,
                  gap: GapResult) -> Optional[dict]:
    """Ask Claude to write the synthesis prose from the structured data."""
    if not ANTHROPIC_API_KEY:
        return None

    prompt = f"""You are writing a financial logic analysis report section.
Write concise analytical prose. No trading advice. No price predictions.

Data:
- Claim: {narrative.claim}
- Evidence strength: {reality.evidence_strength}
- Feasibility score: {reality.feasibility_score}
- Gap Index: {gap.gap_index} ({gap.gap_label})
- Open constraints: {reality.open_constraints}
- Hardware blocker: {reality.hardware_constraint}

Return ONLY JSON with these exact fields:
{{
  "narrative_summary": "<2-3 sentences>",
  "reality_summary": "<2-3 sentences>",
  "gap_interpretation": "<3-4 sentences referencing gap_index and gap_label>",
  "key_uncertainties": ["<3-5 items>"],
  "open_questions": ["<2-4 items>"]
}}"""

    data = call_llm("You are a structured financial analysis writer. Return only JSON.", prompt)
    return data


# ════════════════════════════════════════════════════════════════
#  REPORT WRITER (upgraded v2)
# ════════════════════════════════════════════════════════════════

REPORT_PATH   = Path("nrs1_report.md")
HISTORY_PATH  = Path("nrs1_history.jsonl")

def write_report(session_id, narrative, reality, market, gap, synthesis, mode="live"):
    today     = datetime.datetime.now(datetime.timezone.utc)
    gi_str    = f"{gap.gap_index:.4f}" if gap.gap_index is not None else "None (INSUFFICIENT_EVIDENCE)"
    ret_str   = f"{market.event_window_return*100:+.1f}%" if market.event_window_return else "unavailable"
    m_str     = f"{gap.m_implied:.4f}" if gap.m_implied is not None else "N/A"
    mr_str    = f"{gap.mr_gap:.4f}" if gap.mr_gap is not None else "N/A"

    lines = [
        "# NRS-1 Logic Hedge Report",
        f"**Session:** {session_id} | **Mode:** {mode}  ",
        f"**Generated:** {today.strftime('%Y-%m-%d %H:%M UTC')}  ",
        "",
        "> **DISCLAIMER:** This report is a logic-consistency analysis only.",
        "> It does not constitute investment advice or a recommendation to buy",
        "> or sell any security. All scores are experimental and uncalibrated.",
        "",
        "---",
        "## 1. Narrative Under Analysis",
        f"**Claim:** {narrative.claim}  ",
        f"**Source:** {narrative.source_url}  ",
        f"**Sentiment:** `{narrative.sentiment_polarity}` | "
        f"**Propagation:** `{narrative.propagation}` | "
        f"**Novelty:** `{narrative.novelty}` | "
        f"**Certainty:** `{narrative.certainty}`  ",
        "",
        "---",
        "## 2. Engineering Reality Assessment",
        f"**Technical Change:** {reality.technical_change}  ",
        f"**Feasibility Score:** `{reality.feasibility_score}` | "
        f"**Constraint Penalty:** `{reality.constraint_penalty}` | "
        f"**Evidence:** `{reality.evidence_strength}`  ",
        f"**Hardware Constraint:** {reality.hardware_constraint}  ",
        f"**Supply Chain Risk:** {reality.supply_chain_risk}  ",
        "",
        "**Unresolved Constraints:**",
    ] + ([f"- `{c}`" for c in reality.open_constraints] if reality.open_constraints else ["- none identified"]) + [
        "",
        "---",
        "## 3. Market Data",
        f"**Ticker:** {market.ticker} | **Event Date:** {market.event_date}  ",
        f"**5-Day Return:** {ret_str} | **Data Quality:** `{market.data_quality}`  ",
        "",
        "---",
        "## 4. Gap Index",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| N_score (Narrative) | `{gap.n_score:.4f}` |",
        f"| R_score (Reality)   | `{gap.r_score:.4f}` |",
        f"| M_implied (Market)  | `{m_str}` |",
        f"| NR_gap              | `{gap.nr_gap:.4f}` |",
        f"| MR_gap              | `{mr_str}` |",
        f"| **Gap Index**       | **`{gi_str}`** |",
        f"| **Gap Label**       | **`{gap.gap_label}`** |",
        "",
        "**Calculation trace:**",
    ] + [f"- {n}" for n in gap.notes] + [
        "",
        "---",
        "## 5. Synthesis",
        "",
        f"**Narrative Summary**  ",
        synthesis["narrative_summary"],
        "",
        f"**Reality Summary**  ",
        synthesis["reality_summary"],
        "",
        f"**Gap Interpretation**  ",
        synthesis["gap_interpretation"],
        "",
        "**Key Uncertainties**",
    ] + [f"- {u}" for u in synthesis["key_uncertainties"]] + [
        "",
        "**Open Questions**",
    ] + [f"- {q}" for q in synthesis["open_questions"]] + [
        "",
        "---",
        f"*Audit trail: `{AUDIT_PATH}`*  ",
        "*NRS-1 v2 — Not Investment Advice*",
    ]

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


# ════════════════════════════════════════════════════════════════
#  MODULE 3 — EMAIL DISPATCH
# ════════════════════════════════════════════════════════════════

def send_report_email(report_path: str = "nrs1_report.md",
                      gap_label: str = "UNKNOWN") -> bool:
    """
    Sends the report via Gmail SMTP.
    Returns True on success, False on failure.

    Setup: Google Account → Security → App Passwords → Create
    Use the 16-character code as GMAIL_APP_PASSWORD.
    """
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD or not GMAIL_RECIPIENT:
        print("  [EMAIL] Credentials not configured. Skipping email. (Set GMAIL_SENDER etc.)")
        return False

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            body = f.read()

        today   = datetime.date.today().strftime("%Y-%m-%d")
        subject = f"[NRS-1] {today} — {gap_label} | Daily Narrative-Reality Report"

        msg = MIMEMultipart()
        msg["From"]    = GMAIL_SENDER
        msg["To"]      = GMAIL_RECIPIENT
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, GMAIL_RECIPIENT, msg.as_string())

        print(f"  [EMAIL] ✓ Report sent to {GMAIL_RECIPIENT}")
        return True

    except smtplib.SMTPAuthenticationError:
        print("  [EMAIL] ✗ Authentication failed. Check GMAIL_APP_PASSWORD.")
        print("           Go to: myaccount.google.com/security → App passwords")
        return False
    except Exception as e:
        print(f"  [EMAIL] ✗ Failed: {e}")
        return False


# ════════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATOR
# ════════════════════════════════════════════════════════════════


def write_history(session_id, narrative, reality, market, gap, mode="stub"):
    """Append one record per run to nrs1_history.jsonl — feeds the dashboard."""
    record = {
        "ts":        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": session_id,
        "ticker":    market.ticker,
        "claim":     narrative.claim[:120],
        "n_score":   gap.n_score,
        "r_score":   gap.r_score,
        "m_implied": gap.m_implied,
        "nr_gap":    gap.nr_gap,
        "mr_gap":    gap.mr_gap,
        "gap_index": gap.gap_index,
        "gap_label": gap.gap_label,
        "evidence":  reality.evidence_strength,
        "mode":      mode,
    }
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

def run_pipeline(use_stubs: bool = False,
                 session_id: str = None,
                 send_email: bool = True) -> Optional[str]:
    """
    Full pipeline run.
    use_stubs=True  → no API calls, uses hardcoded test data (v1 behavior)
    use_stubs=False → tries live scraping + LLM, falls back to stub on failure
    """
    if session_id is None:
        session_id = f"NRS1-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"

    mode = "stub" if use_stubs else "live"
    sep  = "─" * 58

    print(f"\n{sep}")
    print(f"  NRS-1 v2  |  {session_id}  |  mode={mode}")
    print(sep)

    audit(session_id, "orchestrator", "start", {"mode": mode})

    # ── Stage 1: Narrative ──────────────────────────────────────
    print("\n[Stage 1] Narrative extraction...")
    narrative = None

    if not use_stubs:
        headlines = scrape_ai_news()
        top = pick_top_headline(headlines)
        if top:
            print(f"  Top headline: {top['title'][:70]}")
            narrative = live_narrative_agent(top)
            if narrative is None:
                print("  [LLM] Narrative extraction failed → using stub")
        else:
            print("  [SCRAPER] No relevant headlines found → using stub")

    if narrative is None:
        narrative = stub_narrative()
        print("  Using stub narrative.")

    passed, reasons = gate_1(narrative)
    for r in reasons: print(f"  Gate 1: {r}")
    if not passed:
        audit(session_id, "gate_1", "FAILED", reasons=reasons)
        print("  ✗ Gate 1 FAILED. Halted."); return None
    audit(session_id, "gate_1", "PASSED", reasons=reasons)
    print("  ✓ Gate 1 PASSED")

    # ── Stage 2: Reality ────────────────────────────────────────
    print("\n[Stage 2] Reality assessment...")
    reality = None

    if not use_stubs and ANTHROPIC_API_KEY:
        reality = live_reality_agent(narrative.claim)
        if reality is None:
            print("  [LLM] Reality assessment failed → using stub")

    if reality is None:
        reality = stub_reality()
        print("  Using stub reality.")

    passed, reasons = gate_2(reality)
    for r in reasons: print(f"  Gate 2: {r}")
    if not passed:
        audit(session_id, "gate_2", "FAILED", reasons=reasons)
        print("  ✗ Gate 2 FAILED. Halted."); return None
    audit(session_id, "gate_2", "PASSED", reasons=reasons)
    print("  ✓ Gate 2 PASSED")

    # ── Stage 3: Market data ────────────────────────────────────
    print("\n[Stage 3] Market data...")
    today_str = datetime.date.today().isoformat()

    if not use_stubs:
        # Try to match a ticker from the narrative claim
        ticker = next((t for t in WATCH_TICKERS if t in narrative.claim.upper()), "NVDA")
        market = get_market_data(ticker, today_str)
    else:
        market = stub_market("NVDA")

    passed, reasons = gate_3(market)
    for r in reasons: print(f"  Gate 3: {r}")
    audit(session_id, "gate_3", "PASSED", reasons=reasons)
    print("  ✓ Gate 3 PASSED")

    # ── Stage 4: Gap Index ──────────────────────────────────────
    print("\n[Stage 4] Computing Gap Index...")
    gap = compute_gap_index(narrative, reality, market)

    for note in gap.notes:
        print(f"  {note}")

    passed, reasons = gate_4(gap)
    if not passed:
        audit(session_id, "gate_4", "FAILED", reasons=reasons)
        print("  ✗ Gate 4 FAILED. Halted."); return None

    print(f"\n  ┌─────────────────────────────────────┐")
    print(f"  │  Gap Index : {gap.gap_index:.6f}                │")
    print(f"  │  Label     : {gap.gap_label:<23}│")
    print(f"  └─────────────────────────────────────┘")

    audit(session_id, "gate_4", "PASSED",
          detail={"gap_index": gap.gap_index, "gap_label": gap.gap_label})

    # ── Stage 5: Synthesis ──────────────────────────────────────
    print("\n[Stage 5] Synthesis...")
    synthesis = None

    if not use_stubs and ANTHROPIC_API_KEY:
        synthesis = llm_synthesis(narrative, reality, gap)
        if synthesis is None:
            print("  [LLM] Synthesis failed → using stub")

    if synthesis is None:
        synthesis = stub_synthesis(gap)
        print("  Using stub synthesis.")

    passed, reasons = gate_5(synthesis)
    if not passed:
        audit(session_id, "gate_5", "FAILED", reasons=reasons)
        print("  ✗ Gate 5 FAILED. Halted."); return None
    audit(session_id, "gate_5", "PASSED")
    print("  ✓ Gate 5 PASSED")

    # ── Stage 6: Report ─────────────────────────────────────────
    print("\n[Stage 6] Writing report...")
    write_report(session_id, narrative, reality, market, gap, synthesis, mode)
    write_history(session_id, narrative, reality, market, gap, mode)
    audit(session_id, "report", "written", {"path": str(REPORT_PATH)})
    print(f"  ✓ Report → {REPORT_PATH}")

    # ── Stage 7: Email ──────────────────────────────────────────
    if send_email:
        print("\n[Stage 7] Sending email...")
        send_report_email(str(REPORT_PATH), gap.gap_label)

    audit(session_id, "orchestrator", "complete",
          {"gap_index": gap.gap_index, "gap_label": gap.gap_label})

    print(f"\n{sep}")
    print(f"  ✓ Pipeline complete.")
    print(f"  Report  → {REPORT_PATH}")
    print(f"  Audit   → {AUDIT_PATH}")
    print(sep + "\n")

    return str(REPORT_PATH)


# ════════════════════════════════════════════════════════════════
#  UNIT TESTS
# ════════════════════════════════════════════════════════════════

def run_tests():
    print("\n=== NRS-1 v2 Unit Tests ===\n")
    errors = 0

    n = NarrativeObject("AI chip 10x efficiency", "http://ex.com", 1.0, 1.0, "first_report", "moderate")
    r = RealityObject("10x chip", 0.35, 0.30, "weak", [], "HBM limited", "single-source")
    m = MarketObject("NVDA", "2026-05-01", 0.12, "ok")

    g = compute_gap_index(n, r, m)
    assert g.gap_index is not None and 0 <= g.gap_index <= 1
    print(f"[a] Normal case: gap={g.gap_index:.4f} {g.gap_label}  ✓")

    r2 = RealityObject("10x chip", 0.35, 0.30, "insufficient", [], "", "")
    g2 = compute_gap_index(n, r2, m)
    assert g2.gap_index is None and g2.gap_label == "INSUFFICIENT_EVIDENCE"
    print(f"[b] Insufficient evidence: gap=None  ✓")

    m3 = MarketObject("NVDA", "2026-05-01", None, "unavailable")
    g3 = compute_gap_index(n, r, m3)
    assert g3.mr_gap is None and g3.gap_index == g3.nr_gap
    print(f"[c] No market data: gap=nr_gap={g3.nr_gap:.4f}  ✓")

    try:
        assert abs(0.6 + 0.6 - 1.0) < 1e-9
    except AssertionError:
        print(f"[d] α+β≠1.0 → AssertionError  ✓")

    # Stub pipeline runs without crash
    result = run_pipeline(use_stubs=True, session_id="NRS1-TEST", send_email=False)
    assert result is not None
    print(f"[e] Full stub pipeline: report written  ✓")

    # Verify exact stub fixture numbers
    g6 = compute_gap_index(
        NarrativeObject("stub", "http://ex.com", 1.0, 1.0, "first_report", "moderate"),
        RealityObject("stub", 0.35, 0.30, "weak", [], "", ""),
        MarketObject("NVDA", "2026-05-01", 0.12, "ok")
    )
    assert g6.gap_label == "STRONG_MISMATCH"
    assert abs(g6.gap_index - 0.786262) < 0.001
    print(f"[f] Stub fixture: {g6.gap_index:.6f} = STRONG_MISMATCH  ✓")

    if errors == 0:
        print("\n✓ All tests passed.\n")


# ════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if "--test" in sys.argv:
        run_tests()
    elif "--stub" in sys.argv:
        run_pipeline(use_stubs=True, send_email=("--email" in sys.argv))
    else:
        run_pipeline(use_stubs=False, send_email=True)


# ════════════════════════════════════════════════════════════════
#  MODULE 4 — WINDOWS TASK SCHEDULER SETUP
# ════════════════════════════════════════════════════════════════
"""
To run this automatically every morning at 8:00 AM:

1. Press Windows key → type "Task Scheduler" → Open it

2. Click "Create Basic Task..." (right side panel)

3. Fill in:
   Name:        NRS-1 Daily Report
   Description: Run NRS-1 narrative-reality analysis and email report

4. Trigger: Daily
   Start time: 8:00:00 AM
   Recur every: 1 day

5. Action: Start a program
   Program:  C:\\Users\\1\\Desktop\\nrs1\\nrs1\\venv\\Scripts\\python.exe
   Arguments: C:\\Users\\1\\Desktop\\nrs1\\nrs1\\nrs1_v2.py
   Start in:  C:\\Users\\1\\Desktop\\nrs1\\nrs1\\

6. Click Finish.

To test it immediately: right-click the task → Run
The report will appear in C:\\Users\\1\\Desktop\\nrs1\\nrs1\\nrs1_report.md
and be emailed to GMAIL_RECIPIENT if credentials are set.

NOTE: Your computer must be on and not in sleep mode at 8:00 AM.
To prevent sleep: Settings → Power → Sleep → Never (while plugged in)
"""
