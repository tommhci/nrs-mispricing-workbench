"""
NRS-1 v3 — Narrative-Reality Mispricing Workbench
==================================================
Full reconstruction from v2. Key changes:
  - LLM: Anthropic → GLM (Zhipu AI, OpenAI-compatible, free tokens)
  - Sources: RSS headlines → Tiered document fetcher
      Tier 1: SEC EDGAR 8-K/10-Q (primary filings, free API)
      Tier 2: Expert analysis RSS (SemiAnalysis, FabricatedKnowledge, Epoch AI)
      Tier 3: General media RSS (v2 feeds, demoted, headline-only)
  - Evidence ceiling: source_tier hard-caps evidence_strength
  - Data models: NarrativeObject + verbatim_quote; RealityObject + evidence_ceiling
  - Gap Index formula: UNCHANGED (fully backward compatible)

SETUP:
  pip install openai requests yfinance pandas streamlit plotly
  export ZHIPU_API_KEY=<from open.bigmodel.cn>

RUN:
  python nrs1_v3.py          -- live mode (GLM + tiered sources)
  python nrs1_v3.py --stub   -- stub mode (no API key needed)
  python nrs1_v3.py --test   -- unit tests

NOT INVESTMENT ADVICE. Scores are experimental and uncalibrated.
See NRS1_v3_SPEC.md for full design documentation.
"""

import json
import math
import re
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
#  ★  CONFIG
# ════════════════════════════════════════════════════════════════

# GLM (Zhipu AI) — replaces Anthropic
ZHIPU_API_KEY  = os.environ.get("ZHIPU_API_KEY", "")
GLM_BASE_URL   = "https://open.bigmodel.cn/api/paas/v4/"
GLM_MODEL      = "glm-4.5"          # or "glm-4.5-flash" for cost savings

# Email (unchanged from v2)
GMAIL_SENDER       = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_RECIPIENT    = os.environ.get("GMAIL_RECIPIENT", "")

# Watch list
WATCH_TICKERS = ["NVDA", "AMD", "TSMC", "INTC", "AVGO", "ASML", "MOD", "SMCI"]
WATCH_TOPICS  = ["AI chip", "GPU", "semiconductor", "datacenter", "liquid cooling",
                 "HBM", "CoWoS", "3nm", "inference", "training cluster"]

# SEC EDGAR CIK map (no leading zeros needed for URL, but kept for clarity)
EDGAR_CIK = {
    "NVDA": "0001045810",
    "AMD":  "0000002488",
    "TSM":  "0001046179",
    "INTC": "0000050863",
    "AVGO": "0001730168",
    "ASML": "0000937556",
    "MOD":  "0000067215",
    "SMCI": "0001375365",
}

HEADERS = {
    "User-Agent": "NRS1-Research-Agent/3.0 (academic; nrs1@research.edu)",
    "Accept":     "application/json, text/html, */*",
}



# ════════════════════════════════════════════════════════════════
#  MODULE 0 — SOURCE DOCUMENT (new in v3)
# ════════════════════════════════════════════════════════════════

@dataclass
class SourceDocument:
    """
    Standardised container for any ingested document.
    source_tier drives evidence_strength ceiling in RealityAgent.
    """
    title:         str
    content:       str        # Full text up to 4000 chars — NOT just headline
    url:           str
    source_name:   str        # "SEC EDGAR", "SemiAnalysis", "Reuters", etc.
    source_tier:   int        # 1=primary filing, 2=expert analysis, 3=general media
    quality_score: float      # 1.0 / 0.75 / 0.4
    doc_type:      str        # "8K", "10Q", "research_note", "news"
    pub_date:      str        # ISO8601 or RSS date string
    ticker_refs:   list = field(default_factory=list)

    def relevance_score(self) -> int:
        """Count of watch-list keywords in title + content."""
        text = (self.title + " " + self.content[:500]).lower()
        return sum(1 for kw in WATCH_TICKERS + WATCH_TOPICS if kw.lower() in text)


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-z]+;', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


# ════════════════════════════════════════════════════════════════
#  MODULE 1 — SOURCE ROUTER (tiered document fetcher)
# ════════════════════════════════════════════════════════════════

# ── Tier 1: SEC EDGAR ────────────────────────────────────────────

def fetch_edgar_filings(ticker: str, days_back: int = 14) -> list[SourceDocument]:
    """
    Fetch recent 8-K and 10-Q filings from SEC EDGAR for one ticker.
    EDGAR API is free — no key required.
    Returns list of SourceDocument (Tier 1, quality=1.0).
    """
    try:
        import requests
    except ImportError:
        return []

    cik = EDGAR_CIK.get(ticker.upper())
    if not cik:
        return []

    results = []
    cutoff  = datetime.date.today() - datetime.timedelta(days=days_back)

    try:
        url  = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []

        data     = resp.json()
        recent   = data.get("filings", {}).get("recent", {})
        forms    = recent.get("form", [])
        dates    = recent.get("filingDate", [])
        accs     = recent.get("accessionNumber", [])
        doc_list = recent.get("primaryDocument", [])

        cik_int = int(cik)

        for i, form in enumerate(forms):
            if form not in ("8-K", "10-Q", "6-K"):
                continue
            try:
                filing_date = datetime.date.fromisoformat(dates[i])
            except (ValueError, IndexError):
                continue
            if filing_date < cutoff:
                break   # filings are newest-first

            acc_clean = accs[i].replace("-", "")
            primary   = doc_list[i] if i < len(doc_list) else ""
            if not primary:
                continue

            doc_url = (f"https://www.sec.gov/Archives/edgar/data/"
                       f"{cik_int}/{acc_clean}/{primary}")

            # Fetch actual document text
            body = ""
            try:
                dr = requests.get(doc_url, headers=HEADERS, timeout=12)
                if dr.status_code == 200:
                    body = _strip_html(dr.text)[:4000]
            except Exception:
                pass

            # Keyword filter — skip filings with zero topic hits
            kw_hits = sum(1 for kw in WATCH_TOPICS if kw.lower() in body.lower())
            ticker_hits = ticker.upper() in body.upper()
            if kw_hits == 0 and not ticker_hits:
                continue

            results.append(SourceDocument(
                title=f"{ticker} {form} {dates[i]}",
                content=body,
                url=doc_url,
                source_name="SEC EDGAR",
                source_tier=1,
                quality_score=1.0,
                doc_type=form.replace("-", ""),
                pub_date=dates[i],
                ticker_refs=[ticker],
            ))

    except Exception as e:
        print(f"  [EDGAR] {ticker}: {e}")

    return results


# ── Tier 2: Expert Analysis RSS ──────────────────────────────────

TIER2_SOURCES = [
    {
        "name":    "SemiAnalysis",
        "rss":     "https://semianalysis.com/feed",
        "quality": 0.75,
        "focus":   ["semiconductor", "yield", "HBM", "CoWoS", "supply chain",
                    "NVDA", "TSM", "TSMC", "AMD"],
    },
    {
        "name":    "FabricatedKnowledge",
        "rss":     "https://www.fabricatedknowledge.com/feed",
        "quality": 0.75,
        "focus":   ["semiconductor", "memory", "DRAM", "WFE", "cycle", "chip"],
    },
    {
        "name":    "EpochAI",
        "rss":     "https://epoch.ai/blog/rss.xml",
        "quality": 0.80,
        "focus":   ["AI", "compute", "benchmark", "scaling", "capabilities",
                    "training", "inference"],
    },
    {
        "name":    "IEEESpectrum",
        "rss":     "https://spectrum.ieee.org/rss",
        "quality": 0.75,
        "focus":   ["chip", "GPU", "AI", "semiconductor", "processor", "nvidia"],
    },
]


def fetch_tier2(max_items: int = 6) -> list[SourceDocument]:
    """
    Fetch from curated expert RSS sources.
    For each matching headline: attempt to fetch full article body.
    Falls back to headline-only if body fetch fails (still marked Tier 2
    only if body is successfully fetched; otherwise degrades to Tier 3).
    """
    try:
        import requests
        import xml.etree.ElementTree as ET
    except ImportError:
        return []

    keywords = [kw.lower() for kw in WATCH_TICKERS + WATCH_TOPICS]
    results  = []

    for src in TIER2_SOURCES:
        try:
            resp = requests.get(src["rss"], headers=HEADERS, timeout=8)
            if resp.status_code != 200:
                continue

            root  = ET.fromstring(resp.content)
            items = root.findall(".//item")
            if not items:
                items = root.findall("{http://www.w3.org/2005/Atom}entry")

            for item in items:
                t_el = (item.find("title") or
                        item.find("{http://www.w3.org/2005/Atom}title"))
                l_el = (item.find("link") or
                        item.find("{http://www.w3.org/2005/Atom}link"))
                d_el = (item.find("pubDate") or
                        item.find("{http://www.w3.org/2005/Atom}updated"))

                if t_el is None:
                    continue
                title = (t_el.text or "").strip()
                url   = (l_el.text if l_el is not None and l_el.text
                         else l_el.get("href", "") if l_el is not None else "")
                date  = d_el.text if d_el is not None else ""

                if not any(kw in title.lower() for kw in keywords):
                    continue

                # Try to fetch full article body
                body = ""
                tier = 2
                if url:
                    try:
                        ar = requests.get(url, headers=HEADERS, timeout=12)
                        if ar.status_code == 200:
                            body = _strip_html(ar.text)[:4000]
                        else:
                            tier = 3   # Degrade: couldn't get body
                    except Exception:
                        tier = 3

                ticker_refs = [t for t in WATCH_TICKERS
                               if t in title.upper() or t in body[:200].upper()]

                results.append(SourceDocument(
                    title=title,
                    content=body,
                    url=url,
                    source_name=src["name"],
                    source_tier=tier,
                    quality_score=src["quality"] if tier == 2 else 0.4,
                    doc_type="research_note",
                    pub_date=date,
                    ticker_refs=ticker_refs,
                ))

                if len(results) >= max_items:
                    return results

        except Exception as e:
            print(f"  [TIER2] {src['name']}: {e}")
            continue

    return results


# ── Tier 3: General Media RSS (v2 logic, demoted) ─────────────────

TIER3_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://hnrss.org/frontpage",
    "https://finance.yahoo.com/rss/headline?s=NVDA",
    "https://finance.yahoo.com/rss/headline?s=AMD",
    "https://finance.yahoo.com/rss/headline?s=TSM",
]


def fetch_tier3(max_items: int = 6) -> list[SourceDocument]:
    """
    Existing v2 RSS logic, demoted to Tier 3.
    Headline-only — no full-text fetch for general media.
    """
    try:
        import requests
        import xml.etree.ElementTree as ET
    except ImportError:
        return []

    keywords = [kw.lower() for kw in WATCH_TICKERS + WATCH_TOPICS]
    results  = []

    for feed_url in TIER3_FEEDS:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=8)
            if resp.status_code != 200:
                continue
            root  = ET.fromstring(resp.content)
            items = root.findall(".//item")
            if not items:
                items = root.findall("{http://www.w3.org/2005/Atom}entry")

            src_name = (feed_url.split("/")[2]
                        .replace("www.", "").replace("feeds.", ""))

            for item in items:
                t_el = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
                l_el = item.find("link") or item.find("{http://www.w3.org/2005/Atom}link")
                d_el = item.find("pubDate") or item.find("{http://www.w3.org/2005/Atom}updated")

                if t_el is None:
                    continue
                title = (t_el.text or "").strip()
                if not any(kw in title.lower() for kw in keywords):
                    continue

                url  = (l_el.text if l_el is not None and l_el.text
                        else l_el.get("href", "") if l_el is not None else "")
                date = d_el.text if d_el is not None else ""
                ticker_refs = [t for t in WATCH_TICKERS if t in title.upper()]

                results.append(SourceDocument(
                    title=title, content="",   # No body fetch for Tier 3
                    url=url, source_name=src_name,
                    source_tier=3, quality_score=0.4,
                    doc_type="news", pub_date=date,
                    ticker_refs=ticker_refs,
                ))

        except Exception:
            continue

        if len(results) >= max_items:
            break

    return results


# ── Main Router ───────────────────────────────────────────────────

def get_best_source() -> Optional[SourceDocument]:
    """
    Main entry point for source intelligence.
    Attempts Tier 1 → Tier 2 → Tier 3 in order.
    Returns highest-relevance SourceDocument, or None (triggers stub).
    """
    print("  [SOURCE] Attempting Tier 1 (SEC EDGAR)...")
    tier1 = []
    for ticker in WATCH_TICKERS[:4]:   # Top 4 most likely to have recent 8-K
        docs = fetch_edgar_filings(ticker)
        tier1.extend(docs)
        if len(tier1) >= 3:
            break

    if tier1:
        best = max(tier1, key=lambda d: d.relevance_score())
        if best.relevance_score() > 0:
            print(f"  [SOURCE] ✓ Tier 1: {best.source_name} — {best.title[:60]}")
            return best

    print("  [SOURCE] Tier 1 empty. Attempting Tier 2 (expert analysis)...")
    tier2 = fetch_tier2()
    if tier2:
        best = max(tier2, key=lambda d: d.relevance_score())
        print(f"  [SOURCE] ✓ Tier 2: {best.source_name} — {best.title[:60]}")
        return best

    print("  [SOURCE] Tier 2 empty. Attempting Tier 3 (general media)...")
    tier3 = fetch_tier3()
    if tier3:
        best = max(tier3, key=lambda d: d.relevance_score())
        print(f"  [SOURCE] ✓ Tier 3: {best.source_name} — {best.title[:60]}")
        return best

    print("  [SOURCE] All tiers empty. Pipeline will use stub.")
    return None



# ════════════════════════════════════════════════════════════════
#  DATA MODELS (updated for v3 — backward compatible with v2 history)
# ════════════════════════════════════════════════════════════════

@dataclass
class NarrativeObject:
    # Core fields (unchanged from v2)
    claim:              str
    source_url:         str
    sentiment_polarity: float   # -1.0 to 1.0
    propagation:        float   # 0.25 / 0.5 / 1.0
    novelty:            str     # "first_report" / "echo" / "stale"
    certainty:          str     # "high" / "moderate" / "low"
    # v3 additions (defaults for backward compat with stub / tests)
    source_tier:        int  = 3
    source_name:        str  = "unknown"
    doc_type:           str  = "news"
    verbatim_quote:     str  = ""   # Exact sentence from source — grounds R assessment


@dataclass
class RealityObject:
    # Core fields (unchanged from v2)
    technical_change:    str
    feasibility_score:   float
    constraint_penalty:  float
    evidence_strength:   str    # ceiling-enforced after LLM returns
    open_constraints:    list
    hardware_constraint: str
    supply_chain_risk:   str
    # v3 additions
    evidence_ceiling:    str  = "strong"   # max allowed by source_tier
    primary_constraint:  str  = ""         # single most binding constraint
    comparable_events:   list = field(default_factory=list)


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
#  CONFIG / WEIGHTS (unchanged from v2)
# ════════════════════════════════════════════════════════════════

ALPHA = 0.5
BETA  = 0.5
assert abs(ALPHA + BETA - 1.0) < 1e-9

NOVELTY_WEIGHT  = {"first_report": 1.0, "echo": 0.6, "stale": 0.3}
EVIDENCE_WEIGHT = {"strong": 1.0, "moderate": 0.7, "weak": 0.4, "insufficient": 0.0}
GAP_THRESHOLDS  = {
    "STRONG_MISMATCH":   0.60,
    "MODERATE_MISMATCH": 0.35,
    "WEAK_MISMATCH":     0.15,
}

# Evidence ceiling map (Tier → max allowed strength)
EVIDENCE_CEILING = {1: "strong", 2: "moderate", 3: "weak"}
EVIDENCE_ORDER   = ["strong", "moderate", "weak", "insufficient"]


def enforce_evidence_ceiling(evidence_str: str, source_tier: int) -> str:
    """
    Hard cap on evidence_strength based on source tier.
    Tier 3 (general media) can never produce 'strong' evidence,
    regardless of what the LLM assesses.
    """
    ceiling = EVIDENCE_CEILING.get(source_tier, "weak")
    ceil_idx = EVIDENCE_ORDER.index(ceiling)
    cur_idx  = (EVIDENCE_ORDER.index(evidence_str)
                if evidence_str in EVIDENCE_ORDER else len(EVIDENCE_ORDER) - 1)
    # Take the weaker (higher index) of ceiling vs LLM output
    return EVIDENCE_ORDER[max(ceil_idx, cur_idx)]



# ════════════════════════════════════════════════════════════════
#  MODULE 2 — LLM AGENTS (GLM via OpenAI-compatible endpoint)
# ════════════════════════════════════════════════════════════════

# ── Updated prompts ───────────────────────────────────────────────

NARRATIVE_PROMPT = """You are a financial narrative extraction agent.
Read the document excerpt and extract the SINGLE most specific, verifiable
engineering or market claim present in the text.

Source context:
- source_tier: {source_tier}  (1=primary filing, 2=expert analysis, 3=general media)
- doc_type:    {doc_type}
- source_name: {source_name}

Return ONLY a JSON object — no markdown, no explanation.

{{
  "claim":              "<single declarative sentence: the specific claim being made>",
  "verbatim_quote":     "<exact sentence or phrase from the document that contains the claim>",
  "sentiment_polarity": <-1.0=strongly negative / -0.5 / 0.0 / 0.5 / 1.0=strongly positive>,
  "propagation":        <0.25=niche / 0.5=moderate coverage / 1.0=major headline>,
  "novelty":            "<first_report|echo|stale>",
  "certainty":          "<high|moderate|low>"
}}

Rules:
- Do NOT invent claims not present in the document
- verbatim_quote must be a real substring from the input text
- sentiment_polarity must be exactly one of: -1.0, -0.5, 0.0, 0.5, 1.0
- propagation must be exactly one of: 0.25, 0.5, 1.0
- novelty must be: first_report | echo | stale
- certainty must be: high | moderate | low
"""

REALITY_PROMPT = """You are an engineering feasibility assessment agent.
Assess the physical and engineering feasibility of the claim below.
Be skeptical — press releases without benchmarks = weak evidence at best.

Claim: "{claim}"
Verbatim source text: "{verbatim_quote}"
Source tier: {source_tier}  (1=primary filing, 2=expert analysis, 3=general media)

Consider: manufacturing yields, supply chain availability, thermal management,
software maturity, capital expenditure requirements, regulatory constraints.

Return ONLY a JSON object — no markdown, no explanation.

{{
  "technical_change":    "<what physical/engineering capability is being claimed>",
  "feasibility_score":   <0.0=impossible / 0.25=no path / 0.5=plausible / 0.75=likely / 1.0=demonstrated>,
  "constraint_penalty":  <0.0–0.9, total penalty for unresolved constraints>,
  "evidence_strength":   "<strong|moderate|weak|insufficient>",
  "open_constraints":    ["<constraint 1>", "<constraint 2>"],
  "hardware_constraint": "<key hardware bottleneck or 'none identified'>",
  "supply_chain_risk":   "<supply chain risk description or 'low'>",
  "primary_constraint":  "<the single most binding constraint>",
  "comparable_events":   ["<historical analogue, e.g. NVDA Blackwell delay 2024>"]
}}

Rules:
- feasibility_score − constraint_penalty >= 0 (penalty cannot exceed score)
- evidence_strength: strong | moderate | weak | insufficient
"""


# ── GLM client (replaces Anthropic) ──────────────────────────────

def call_llm(system_prompt: str, user_content: str,
             model: str = GLM_MODEL) -> Optional[dict]:
    """
    Calls GLM via OpenAI-compatible endpoint.
    Returns parsed JSON dict, or None on any failure.
    Retries once on JSON parse error.

    Migration note: This is a drop-in replacement for the Anthropic
    call_llm() in nrs1_v2.py. Caller interface is identical.
    """
    if not ZHIPU_API_KEY:
        print("  [LLM] ZHIPU_API_KEY not set — returning None (use --stub)")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        print("  [LLM] openai not installed. Run: pip install openai>=1.0.0")
        return None

    client = OpenAI(api_key=ZHIPU_API_KEY, base_url=GLM_BASE_URL)

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=640,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_content},
                ],
            )
            raw = response.choices[0].message.content.strip()
            # Strip markdown code fences if present
            raw = raw.removeprefix("```json").removeprefix("```")
            raw = raw.removesuffix("```").strip()
            return json.loads(raw)

        except json.JSONDecodeError as e:
            print(f"  [LLM] JSON parse failed attempt {attempt + 1}: {e}")
            if attempt == 1:
                return None
        except Exception as e:
            print(f"  [LLM] API call failed: {e}")
            return None

    return None


# ── Narrative Agent ───────────────────────────────────────────────

def live_narrative_agent(document: SourceDocument) -> Optional[NarrativeObject]:
    """
    Extract NarrativeObject from a SourceDocument using GLM.
    Uses full document content (not just headline) for Tier 1/2.
    """
    content_preview = (document.content or document.title)[:3000]
    print(f"  [LLM] NarrativeAgent: {document.source_name} Tier {document.source_tier}"
          f" — {document.title[:55]}...")

    system = NARRATIVE_PROMPT.format(
        source_tier=document.source_tier,
        doc_type=document.doc_type,
        source_name=document.source_name,
    )
    user = (f'Document title: "{document.title}"\n\n'
            f'Document content:\n{content_preview}\n\n'
            f'Extract the most specific, verifiable engineering or market claim.')

    data = call_llm(system, user)
    if data is None:
        return None

    try:
        return NarrativeObject(
            claim=data["claim"],
            source_url=document.url,
            sentiment_polarity=float(data["sentiment_polarity"]),
            propagation=float(data["propagation"]),
            novelty=data["novelty"],
            certainty=data["certainty"],
            source_tier=document.source_tier,
            source_name=document.source_name,
            doc_type=document.doc_type,
            verbatim_quote=data.get("verbatim_quote", ""),
        )
    except (KeyError, ValueError) as e:
        print(f"  [LLM] Narrative schema error: {e}")
        return None


# ── Reality Agent ─────────────────────────────────────────────────

def live_reality_agent(narrative: NarrativeObject) -> Optional[RealityObject]:
    """
    Assess engineering feasibility of the claim using GLM.
    Applies evidence ceiling enforcement after LLM returns.
    """
    print(f"  [LLM] RealityAgent: {narrative.claim[:60]}...")

    system = "You are an engineering feasibility assessment agent. Return only JSON."
    user   = REALITY_PROMPT.format(
        claim=narrative.claim,
        verbatim_quote=narrative.verbatim_quote or "(no verbatim quote available)",
        source_tier=narrative.source_tier,
    )

    data = call_llm(system, user)
    if data is None:
        return None

    try:
        fs = float(data["feasibility_score"])
        cp = float(data["constraint_penalty"])
        cp = min(cp, fs)   # penalty cannot exceed score

        # Apply evidence ceiling based on source tier
        raw_evidence = data.get("evidence_strength", "insufficient")
        ceiling      = EVIDENCE_CEILING.get(narrative.source_tier, "weak")
        capped       = enforce_evidence_ceiling(raw_evidence, narrative.source_tier)

        return RealityObject(
            technical_change=data["technical_change"],
            feasibility_score=fs,
            constraint_penalty=cp,
            evidence_strength=capped,
            open_constraints=data.get("open_constraints", []),
            hardware_constraint=data.get("hardware_constraint", "not assessed"),
            supply_chain_risk=data.get("supply_chain_risk", "not assessed"),
            evidence_ceiling=ceiling,
            primary_constraint=data.get("primary_constraint", ""),
            comparable_events=data.get("comparable_events", []),
        )
    except (KeyError, ValueError) as e:
        print(f"  [LLM] Reality schema error: {e}")
        return None


# ── Market Data (unchanged from v2) ──────────────────────────────

def get_market_data(ticker: str, event_date: str) -> MarketObject:
    """Fetch 5-day return via yfinance. Falls back to stub if unavailable."""
    try:
        import yfinance as yf

        t     = yf.Ticker(ticker)
        start = datetime.datetime.strptime(event_date, "%Y-%m-%d")
        end   = start + datetime.timedelta(days=10)
        hist  = t.history(start=start.strftime("%Y-%m-%d"),
                          end=end.strftime("%Y-%m-%d"),
                          auto_adjust=True)

        if len(hist) >= 2:
            ret = float(hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1.0)
            print(f"  [MARKET] {ticker}: {ret*100:+.1f}% over {len(hist)} days")
            return MarketObject(ticker=ticker, event_date=event_date,
                                event_window_return=ret, data_quality="ok")
        else:
            return MarketObject(ticker=ticker, event_date=event_date,
                                event_window_return=None, data_quality="unavailable")
    except ImportError:
        print("  [MARKET] yfinance not installed — using stub")
        return MarketObject(ticker=ticker, event_date=event_date,
                            event_window_return=None, data_quality="unavailable")
    except Exception as e:
        print(f"  [MARKET] {e}")
        return MarketObject(ticker=ticker, event_date=event_date,
                            event_window_return=None, data_quality="unavailable")


# ── LLM Synthesis (updated for v3 fields) ────────────────────────

def llm_synthesis(narrative: NarrativeObject, reality: RealityObject,
                  gap: GapResult) -> Optional[dict]:
    """Generate synthesis prose via GLM. Falls back to stub_synthesis()."""
    if not ZHIPU_API_KEY:
        return None

    sys_prompt = "You are a structured financial analysis writer. Return only JSON."
    user_prompt = f"""Write a concise analytical report section. No trading advice.

Data:
- Claim: {narrative.claim}
- Source: {narrative.source_name} (Tier {narrative.source_tier})
- Verbatim quote: {narrative.verbatim_quote or "(none)"}
- Evidence strength: {reality.evidence_strength} (ceiling: {reality.evidence_ceiling})
- Feasibility score: {reality.feasibility_score}
- Gap Index: {gap.gap_index} ({gap.gap_label})
- Open constraints: {reality.open_constraints}
- Primary constraint: {reality.primary_constraint}
- Comparable events: {reality.comparable_events}

Return ONLY JSON:
{{
  "narrative_summary":  "<2-3 sentences>",
  "reality_summary":    "<2-3 sentences>",
  "gap_interpretation": "<3-4 sentences referencing gap_index and gap_label>",
  "key_uncertainties":  ["<3-5 items>"],
  "open_questions":     ["<2-4 items>"]
}}"""

    return call_llm(sys_prompt, user_prompt)



# ════════════════════════════════════════════════════════════════
#  GAP INDEX (unchanged from v2 — formula is the same)
# ════════════════════════════════════════════════════════════════

def sigmoid_proxy(r: float, scale: float = 10.0) -> float:
    return 1.0 / (1.0 + math.exp(-scale * r))


def get_gap_label(gi: Optional[float]) -> str:
    if gi is None:    return "INSUFFICIENT_EVIDENCE"
    if gi >= 0.60:    return "STRONG_MISMATCH"
    if gi >= 0.35:    return "MODERATE_MISMATCH"
    if gi >= 0.15:    return "WEAK_MISMATCH"
    return "ALIGNED"


def compute_gap_index(n: NarrativeObject, r: RealityObject,
                      m: MarketObject) -> GapResult:
    notes = []

    # N_score
    nw    = NOVELTY_WEIGHT[n.novelty]
    n_raw = max(-1.0, min(1.0, n.sentiment_polarity * n.propagation * nw))
    n_score = 0.5 + 0.5 * n_raw
    notes.append(f"N_score: {n.sentiment_polarity}×{n.propagation}×{nw} → {n_score:.4f}")

    # R_score (evidence ceiling already applied before this point)
    ew = EVIDENCE_WEIGHT[r.evidence_strength]
    if ew == 0.0:
        notes.append("evidence=insufficient → gap_index=None")
        return GapResult(n_score=n_score, r_score=0.0, m_implied=None,
                         nr_gap=0.0, mr_gap=None, gap_index=None,
                         gap_label="INSUFFICIENT_EVIDENCE", notes=notes)

    r_score = max(0.0, min(1.0,
                           r.feasibility_score * (1.0 - r.constraint_penalty) * ew))
    notes.append(f"R_score: {r.feasibility_score}×(1−{r.constraint_penalty})×{ew}"
                 f" = {r_score:.4f}")

    nr_gap = abs(n_score - r_score)
    notes.append(f"NR_gap: |{n_score:.4f}−{r_score:.4f}| = {nr_gap:.4f}")

    # M_implied
    m_implied = mr_gap = None
    if m.event_window_return is not None:
        m_implied = sigmoid_proxy(m.event_window_return)
        mr_gap    = abs(m_implied - r_score)
        notes.append(f"M_implied: sigmoid({m.event_window_return:.2f})={m_implied:.4f}")
        notes.append(f"MR_gap: {mr_gap:.4f}")
    else:
        notes.append("M_implied: unavailable")

    gap_index = (round(ALPHA * nr_gap + BETA * mr_gap, 6)
                 if mr_gap is not None else round(nr_gap, 6))
    notes.append(f"GapIndex: {gap_index:.6f}")

    return GapResult(
        n_score=round(n_score, 4),   r_score=round(r_score, 4),
        m_implied=round(m_implied, 4) if m_implied else None,
        nr_gap=round(nr_gap, 4),
        mr_gap=round(mr_gap, 4) if mr_gap else None,
        gap_index=gap_index,
        gap_label=get_gap_label(gap_index),
        notes=notes,
    )


# ════════════════════════════════════════════════════════════════
#  GATE CHECKER (unchanged from v2)
# ════════════════════════════════════════════════════════════════

def gate_1(n: NarrativeObject):
    r, p = [], True
    if not n.claim or len(n.claim) < 10:
        r.append("BLOCK: claim too short"); p = False
    if not n.source_url:
        r.append("WARN: no source URL")
    if not n.verbatim_quote:
        r.append("WARN: no verbatim_quote — LLM may have inferred claim")
    return p, r


def gate_2(r: RealityObject):
    reasons, passed = [], True
    if not r.technical_change:
        reasons.append("BLOCK: no technical_change"); passed = False
    if r.evidence_strength == "insufficient":
        reasons.append("WARN: evidence=insufficient")
    if r.open_constraints:
        reasons.append(f"WARN: {len(r.open_constraints)} open constraints")
    if r.evidence_ceiling != r.evidence_strength:
        reasons.append(f"INFO: evidence ceiling applied "
                       f"({r.evidence_ceiling} → {r.evidence_strength})")
    return passed, reasons


def gate_3(m: MarketObject):
    r = []
    if m.data_quality == "unavailable":
        r.append("WARN: no market data — MR_gap excluded from Gap Index")
    return True, r


def gate_4(g: GapResult):
    r, p = [], True
    if g.gap_index is None and g.gap_label != "INSUFFICIENT_EVIDENCE":
        r.append("BLOCK: inconsistent gap_index/label"); p = False
    if g.gap_index is not None and not (0 <= g.gap_index <= 1):
        r.append("BLOCK: gap_index out of [0,1]"); p = False
    return p, r


def gate_5(s: dict):
    r, p = [], True
    for k in ("narrative_summary", "reality_summary", "gap_interpretation"):
        if not s.get(k) or len(s[k]) < 20:
            r.append(f"BLOCK: {k} too short"); p = False
    return p, r


# ════════════════════════════════════════════════════════════════
#  AUDIT LOGGER (unchanged from v2)
# ════════════════════════════════════════════════════════════════

AUDIT_PATH = Path("nrs1_audit.jsonl")


def audit(session_id, stage, event, detail=None, reasons=None):
    entry = {
        "ts":          datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id":  session_id,
        "stage":       stage,
        "event":       event,
        "detail":      detail,
        "gate_reasons": reasons,
    }
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    print(f"  [AUDIT] {stage} | {event}")


# ════════════════════════════════════════════════════════════════
#  STUB AGENTS (fallback when LLM/sources unavailable)
# ════════════════════════════════════════════════════════════════

def stub_source() -> SourceDocument:
    return SourceDocument(
        title="NVIDIA 8-K: Blackwell architecture production ramp update",
        content=("NVIDIA Corporation announces that volume production of its "
                 "Blackwell GPU architecture has commenced at TSMC N4P node. "
                 "Management guidance indicates initial system deliveries to "
                 "hyperscaler customers beginning Q1 2027, subject to CoWoS "
                 "packaging capacity constraints at TSMC advanced packaging facilities. "
                 "The company notes that HBM3e memory supply remains the primary "
                 "gating factor for H2 2026 shipment volumes."),
        url="https://example.com/stub-8k",
        source_name="SEC EDGAR (stub)",
        source_tier=1,
        quality_score=1.0,
        doc_type="8K",
        pub_date=datetime.date.today().isoformat(),
        ticker_refs=["NVDA"],
    )


def stub_narrative(custom_claim=None) -> NarrativeObject:
    return NarrativeObject(
        claim=(custom_claim or
               "NVIDIA Blackwell GPU volume production commences Q1 2027, "
               "with CoWoS packaging and HBM3e supply as primary constraints."),
        source_url="https://example.com/stub-8k",
        sentiment_polarity=0.5,
        propagation=1.0,
        novelty="first_report",
        certainty="moderate",
        source_tier=1,
        source_name="SEC EDGAR (stub)",
        doc_type="8K",
        verbatim_quote=("initial system deliveries to hyperscaler customers "
                        "beginning Q1 2027, subject to CoWoS packaging capacity constraints"),
    )


def stub_reality() -> RealityObject:
    return RealityObject(
        technical_change="Blackwell GPU volume production at TSMC N4P with CoWoS packaging",
        feasibility_score=0.55,
        constraint_penalty=0.30,
        evidence_strength="moderate",
        open_constraints=[
            "CoWoS packaging capacity limited through H2 2026",
            "HBM3e supply constrained by SK Hynix / Micron ramp",
            "N4P yield at volume not publicly verified",
        ],
        hardware_constraint="CoWoS advanced packaging throughput at TSMC",
        supply_chain_risk="HBM3e single-source risk; packaging shared with AMD MI400",
        evidence_ceiling="strong",
        primary_constraint="CoWoS packaging capacity",
        comparable_events=["NVDA H100 CoWoS shortage 2023", "NVDA Hopper delay 2022"],
    )


def stub_market(ticker="NVDA") -> MarketObject:
    return MarketObject(
        ticker=ticker,
        event_date=datetime.date.today().isoformat(),
        event_window_return=0.08,
        data_quality="ok",
    )


def stub_synthesis(gap: GapResult) -> dict:
    gi = gap.gap_index or 0
    return {
        "narrative_summary": (
            "NVIDIA's Blackwell production announcement frames volume delivery "
            "as imminent, implying rapid hyperscaler deployment and accelerated "
            "data center capital expenditure through 2027."),
        "reality_summary": (
            "Engineering evidence is moderate: the filing confirms production "
            "commencement but explicitly flags CoWoS packaging and HBM3e supply "
            "as binding constraints, suggesting the delivery timeline carries "
            "material execution risk."),
        "gap_interpretation": (
            f"Gap Index = {gi:.4f} ({gap.gap_label}). "
            f"N_score={gap.n_score:.3f} moderately exceeds R_score={gap.r_score:.3f}. "
            "The narrative emphasises production commencement while understating "
            "the packaging and memory constraints that govern actual shipment volumes. "
            f"Market pricing (M={gap.m_implied or 'N/A'}) has partially absorbed "
            "the announcement."),
        "key_uncertainties": [
            "CoWoS capacity expansion timeline at TSMC not publicly committed.",
            "HBM3e supply allocation between NVDA and AMD not disclosed.",
            "N4P yield rate at Blackwell volume not independently verified.",
            "Hyperscaler customer acceptance testing timelines unknown.",
        ],
        "open_questions": [
            "What volume constitutes 'commencement' — engineering samples or full ramp?",
            "Has any hyperscaler publicly confirmed Q1 2027 Blackwell deployment?",
            "What is TSMC's CoWoS capacity commitment specifically for NVDA?",
        ],
    }



# ════════════════════════════════════════════════════════════════
#  REPORT WRITER (updated for v3 fields)
# ════════════════════════════════════════════════════════════════

REPORT_PATH  = Path("nrs1_report.md")
HISTORY_PATH = Path("nrs1_history.jsonl")


def write_report(session_id, narrative, reality, market, gap, synthesis, mode="live"):
    today   = datetime.datetime.now(datetime.timezone.utc)
    gi_str  = (f"{gap.gap_index:.4f}" if gap.gap_index is not None
               else "None (INSUFFICIENT_EVIDENCE)")
    ret_str = (f"{market.event_window_return*100:+.1f}%"
               if market.event_window_return else "unavailable")
    m_str   = f"{gap.m_implied:.4f}" if gap.m_implied is not None else "N/A"
    mr_str  = f"{gap.mr_gap:.4f}"   if gap.mr_gap   is not None else "N/A"

    tier_label = {1: "Tier 1 — Primary Filing",
                  2: "Tier 2 — Expert Analysis",
                  3: "Tier 3 — General Media"}.get(narrative.source_tier, "Unknown")
    ceiling_note = ""
    if reality.evidence_ceiling != reality.evidence_strength:
        ceiling_note = (f"  *(evidence ceiling applied: "
                        f"{reality.evidence_ceiling} → {reality.evidence_strength})*")

    lines = [
        "# NRS-1 v3 Logic Hedge Report",
        f"**Session:** `{session_id}` | **Mode:** `{mode}`  ",
        f"**Generated:** {today.strftime('%Y-%m-%d %H:%M UTC')}  ",
        "",
        "> **DISCLAIMER:** This report is a logic-consistency analysis only.",
        "> It does not constitute investment advice or a recommendation to buy",
        "> or sell any security. All scores are experimental and uncalibrated.",
        "",
        "---",
        "## 1. Source Document",
        f"**Source:** {narrative.source_name} · **{tier_label}** · `{narrative.doc_type}`  ",
        f"**URL:** {narrative.source_url}  ",
        "",
        "---",
        "## 2. Narrative Under Analysis",
        f"**Claim:** {narrative.claim}  ",
        f"**Verbatim Quote:** *\"{narrative.verbatim_quote}\"*  ",
        f"**Sentiment:** `{narrative.sentiment_polarity}` | "
        f"**Propagation:** `{narrative.propagation}` | "
        f"**Novelty:** `{narrative.novelty}` | "
        f"**Certainty:** `{narrative.certainty}`  ",
        "",
        "---",
        "## 3. Engineering Reality Assessment",
        f"**Technical Change:** {reality.technical_change}  ",
        f"**Feasibility Score:** `{reality.feasibility_score}` | "
        f"**Constraint Penalty:** `{reality.constraint_penalty}` | "
        f"**Evidence:** `{reality.evidence_strength}`{ceiling_note}  ",
        f"**Hardware Constraint:** {reality.hardware_constraint}  ",
        f"**Supply Chain Risk:** {reality.supply_chain_risk}  ",
        f"**Primary Constraint:** {reality.primary_constraint}  ",
        "",
        "**Unresolved Constraints:**",
    ] + ([f"- `{c}`" for c in reality.open_constraints]
         if reality.open_constraints else ["- none identified"]) + [
        "",
        "**Historical Analogues:**",
    ] + ([f"- {e}" for e in reality.comparable_events]
         if reality.comparable_events else ["- none identified"]) + [
        "",
        "---",
        "## 4. Market Data",
        f"**Ticker:** {market.ticker} | **Event Date:** {market.event_date}  ",
        f"**5-Day Return:** {ret_str} | **Data Quality:** `{market.data_quality}`  ",
        "",
        "---",
        "## 5. Gap Index",
        "",
        "| Metric          | Value |",
        "|-----------------|-------|",
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
        "## 6. Synthesis",
        "",
        "**Narrative Summary**  ",
        synthesis["narrative_summary"],
        "",
        "**Reality Summary**  ",
        synthesis["reality_summary"],
        "",
        "**Gap Interpretation**  ",
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
        "*NRS-1 v3 — Not Investment Advice*",
    ]

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [REPORT] Written to {REPORT_PATH}")


def write_history(session_id, narrative, reality, market, gap, mode="stub"):
    """Append one record per run. Feeds the Streamlit dashboard."""
    record = {
        "ts":           datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id":   session_id,
        "ticker":       market.ticker,
        "claim":        narrative.claim[:120],
        "n_score":      gap.n_score,
        "r_score":      gap.r_score,
        "m_implied":    gap.m_implied,
        "nr_gap":       gap.nr_gap,
        "mr_gap":       gap.mr_gap,
        "gap_index":    gap.gap_index,
        "gap_label":    gap.gap_label,
        "evidence":     reality.evidence_strength,
        "mode":         mode,
        # v3 new fields
        "source_tier":  narrative.source_tier,
        "source_name":  narrative.source_name,
        "doc_type":     narrative.doc_type,
        "verbatim":     narrative.verbatim_quote[:100] if narrative.verbatim_quote else "",
        "ev_ceiling":   reality.evidence_ceiling,
    }
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    print(f"  [HISTORY] Record appended (gap_index={gap.gap_index})")


# ════════════════════════════════════════════════════════════════
#  MODULE 3 — EMAIL DISPATCH (unchanged from v2)
# ════════════════════════════════════════════════════════════════

def send_report_email(report_path: str = "nrs1_report.md",
                      gap_label: str = "UNKNOWN") -> bool:
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD or not GMAIL_RECIPIENT:
        print("  [EMAIL] Credentials not set. Skipping.")
        return False
    try:
        body    = Path(report_path).read_text(encoding="utf-8")
        today   = datetime.date.today().strftime("%Y-%m-%d")
        subject = f"[NRS-1 v3] {today} — {gap_label} | Logic Hedge Report"

        msg = MIMEMultipart()
        msg["From"]    = GMAIL_SENDER
        msg["To"]      = GMAIL_RECIPIENT
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, GMAIL_RECIPIENT, msg.as_string())

        print(f"  [EMAIL] ✓ Sent to {GMAIL_RECIPIENT}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("  [EMAIL] ✗ Auth failed. Check GMAIL_APP_PASSWORD.")
        return False
    except Exception as e:
        print(f"  [EMAIL] ✗ Failed: {e}")
        return False



# ════════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATOR
# ════════════════════════════════════════════════════════════════

def run_pipeline(mode: str = "stub") -> dict:
    """
    Main pipeline. Returns summary dict.

    mode="stub"  — hardcoded data, no API key needed
    mode="live"  — real GLM calls + real sources
    """
    session_id = datetime.datetime.now(datetime.timezone.utc).strftime("NRS3-%Y%m%d-%H%M%S")
    print(f"\n{'='*60}")
    print(f"  NRS-1 v3 | session={session_id} | mode={mode}")
    print(f"{'='*60}")

    audit(session_id, "INIT", "pipeline_start", detail={"mode": mode})

    # ── Stage 0: Source Acquisition ──────────────────────────────
    document = None
    if mode == "live":
        document = get_best_source()

    if document is None:
        if mode == "live":
            print("  [PIPELINE] Source acquisition failed — using stub")
            audit(session_id, "SOURCE", "all_tiers_failed_using_stub")
        document = stub_source()

    audit(session_id, "SOURCE", "document_acquired",
          detail={"tier": document.source_tier, "source": document.source_name,
                  "title": document.title[:60]})

    # ── Stage 1: Narrative Extraction ────────────────────────────
    narrative = None
    if mode == "live":
        narrative = live_narrative_agent(document)

    if narrative is None:
        if mode == "live":
            print("  [PIPELINE] NarrativeAgent failed — using stub")
        narrative = stub_narrative()

    passed, reasons = gate_1(narrative)
    audit(session_id, "GATE_1", "passed" if passed else "blocked", reasons=reasons)
    if not passed:
        print(f"  [GATE 1] BLOCKED: {reasons}")
        return {"status": "blocked", "gate": 1, "reasons": reasons}

    # ── Stage 2: Reality Assessment ──────────────────────────────
    reality = None
    if mode == "live":
        reality = live_reality_agent(narrative)

    if reality is None:
        if mode == "live":
            print("  [PIPELINE] RealityAgent failed — using stub")
        reality = stub_reality()

    passed, reasons = gate_2(reality)
    audit(session_id, "GATE_2", "passed" if passed else "blocked", reasons=reasons)
    if not passed:
        print(f"  [GATE 2] BLOCKED: {reasons}")
        return {"status": "blocked", "gate": 2, "reasons": reasons}

    # ── Stage 3: Market Data ─────────────────────────────────────
    ticker = (narrative.ticker_refs[0] if hasattr(narrative, "ticker_refs")
              and narrative.ticker_refs else "NVDA")
    # Use first ticker_ref from document if available
    if document.ticker_refs:
        ticker = document.ticker_refs[0]

    event_date = datetime.date.today().isoformat()

    if mode == "live":
        market = get_market_data(ticker, event_date)
    else:
        market = stub_market(ticker)

    passed, reasons = gate_3(market)
    audit(session_id, "GATE_3", "passed", reasons=reasons)

    # ── Stage 4: Gap Index ───────────────────────────────────────
    gap = compute_gap_index(narrative, reality, market)
    audit(session_id, "GAP_INDEX", gap.gap_label,
          detail={"gap_index": gap.gap_index, "n_score": gap.n_score,
                  "r_score": gap.r_score})

    passed, reasons = gate_4(gap)
    audit(session_id, "GATE_4", "passed" if passed else "blocked", reasons=reasons)
    if not passed:
        print(f"  [GATE 4] BLOCKED: {reasons}")
        return {"status": "blocked", "gate": 4, "reasons": reasons}

    # ── Stage 5: Synthesis ───────────────────────────────────────
    synthesis = None
    if mode == "live":
        synthesis = llm_synthesis(narrative, reality, gap)
    if synthesis is None:
        synthesis = stub_synthesis(gap)

    passed, reasons = gate_5(synthesis)
    audit(session_id, "GATE_5", "passed" if passed else "blocked", reasons=reasons)
    if not passed:
        print(f"  [GATE 5] BLOCKED — rebuilding with stub synthesis")
        synthesis = stub_synthesis(gap)

    # ── Stage 6: Output ──────────────────────────────────────────
    write_report(session_id, narrative, reality, market, gap, synthesis, mode=mode)
    write_history(session_id, narrative, reality, market, gap, mode=mode)
    audit(session_id, "OUTPUT", "written",
          detail={"gap_index": gap.gap_index, "label": gap.gap_label})

    # ── Stage 7: Email ───────────────────────────────────────────
    send_report_email(gap_label=gap.gap_label)

    print(f"\n  ✓ Pipeline complete | GapIndex={gap.gap_index} | {gap.gap_label}")
    print(f"    Source: {narrative.source_name} (Tier {narrative.source_tier})")
    print(f"    Evidence: {reality.evidence_strength}"
          f" (ceiling: {reality.evidence_ceiling})")
    print(f"    Report: {REPORT_PATH}")

    return {
        "status":      "ok",
        "gap_index":   gap.gap_index,
        "gap_label":   gap.gap_label,
        "source_tier": narrative.source_tier,
        "source_name": narrative.source_name,
        "evidence":    reality.evidence_strength,
    }


# ════════════════════════════════════════════════════════════════
#  UNIT TESTS
# ════════════════════════════════════════════════════════════════

def run_tests():
    print("\n" + "="*55)
    print("  NRS-1 v3 — Unit Tests")
    print("="*55)
    passed = 0
    failed = 0

    def check(name, condition, detail=""):
        nonlocal passed, failed
        if condition:
            print(f"  ✓ {name}")
            passed += 1
        else:
            print(f"  ✗ {name}  {detail}")
            failed += 1

    # T1: Evidence ceiling enforcement
    check("T1a enforce_ceiling Tier3 strong → weak",
          enforce_evidence_ceiling("strong", 3) == "weak")
    check("T1b enforce_ceiling Tier2 strong → moderate",
          enforce_evidence_ceiling("strong", 2) == "moderate")
    check("T1c enforce_ceiling Tier1 strong → strong",
          enforce_evidence_ceiling("strong", 1) == "strong")
    check("T1d enforce_ceiling Tier3 weak → weak",
          enforce_evidence_ceiling("weak", 3) == "weak")
    check("T1e enforce_ceiling Tier2 weak → weak",
          enforce_evidence_ceiling("weak", 2) == "weak")

    # T2: N_score calculation
    n = stub_narrative()
    r = stub_reality()
    m = stub_market()
    gap = compute_gap_index(n, r, m)
    check("T2a gap_index in [0,1]",
          gap.gap_index is not None and 0 <= gap.gap_index <= 1,
          f"got {gap.gap_index}")
    check("T2b n_score in [0,1]",
          0 <= gap.n_score <= 1, f"got {gap.n_score}")
    check("T2c r_score in [0,1]",
          0 <= gap.r_score <= 1, f"got {gap.r_score}")
    check("T2d gap_label is a valid string",
          gap.gap_label in ("STRONG_MISMATCH", "MODERATE_MISMATCH",
                            "WEAK_MISMATCH", "ALIGNED", "INSUFFICIENT_EVIDENCE"))

    # T3: Gate checkers
    g1p, _ = gate_1(n)
    check("T3a gate_1 passes on stub narrative", g1p)
    g2p, _ = gate_2(r)
    check("T3b gate_2 passes on stub reality", g2p)
    g4p, _ = gate_4(gap)
    check("T3c gate_4 passes on computed gap", g4p)

    # T4: Insufficient evidence short-circuits
    r_insuf = stub_reality()
    r_insuf.evidence_strength = "insufficient"
    gap_none = compute_gap_index(n, r_insuf, m)
    check("T4a insufficient evidence → gap_index=None",
          gap_none.gap_index is None)
    check("T4b insufficient evidence → INSUFFICIENT_EVIDENCE label",
          gap_none.gap_label == "INSUFFICIENT_EVIDENCE")

    # T5: Source document relevance scoring
    doc = stub_source()
    check("T5a stub_source relevance_score > 0",
          doc.relevance_score() > 0, f"got {doc.relevance_score()}")
    check("T5b stub_source is Tier 1", doc.source_tier == 1)

    # T6: History record keys
    write_history("test-session", n, r, stub_market(), gap, mode="test")
    import linecache
    history_lines = HISTORY_PATH.read_text(encoding="utf-8").strip().split("\n")
    last = json.loads(history_lines[-1])
    for key in ("source_tier", "source_name", "doc_type", "ev_ceiling"):
        check(f"T6 history record has key '{key}'", key in last)

    print(f"\n  Results: {passed} passed, {failed} failed")
    return failed == 0


# ════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--test" in args or "--test-llm" in args:
        # GLM connectivity test
        if "--test-llm" in args:
            print("\n[TEST-LLM] Calling GLM with a simple prompt...")
            result = call_llm(
                "Return only JSON: {\"status\": \"ok\", \"model\": \"glm\"}",
                "Test call. Return the JSON exactly as specified in the system prompt."
            )
            print(f"  Result: {result}")
            sys.exit(0 if result else 1)

        ok = run_tests()
        sys.exit(0 if ok else 1)

    elif "--test-edgar" in args:
        idx = args.index("--test-edgar")
        ticker = args[idx + 1] if idx + 1 < len(args) else "NVDA"
        print(f"\n[TEST-EDGAR] Fetching EDGAR filings for {ticker}...")
        docs = fetch_edgar_filings(ticker, days_back=30)
        print(f"  Found {len(docs)} relevant filings:")
        for d in docs[:3]:
            print(f"  - {d.title} | relevance={d.relevance_score()} | {len(d.content)} chars")
        sys.exit(0 if docs else 1)

    elif "--stub" in args:
        result = run_pipeline(mode="stub")
        sys.exit(0 if result["status"] == "ok" else 1)

    else:
        # Default: live mode
        result = run_pipeline(mode="live")
        sys.exit(0 if result["status"] == "ok" else 1)
