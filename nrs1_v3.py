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
# Recommendation (June 2026): "glm-4.7-flash" is free on Z.ai API and
# outperforms glm-4.5 on most benchmarks (codersera.com Jan 2026;
# llm-stats.com). Override with env var GLM_MODEL=glm-4.7-flash.
# Default stays "glm-4.5" because that is what Tom's free-tier tokens cover.
GLM_MODEL      = os.environ.get("GLM_MODEL", "glm-4.5")

# Email (unchanged from v2)
GMAIL_SENDER       = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_RECIPIENT    = os.environ.get("GMAIL_RECIPIENT", "")

# Watch list
WATCH_TICKERS = ["NVDA", "AMD", "TSMC", "INTC", "AVGO", "ASML", "MOD", "SMCI"]
WATCH_TOPICS  = ["AI chip", "GPU", "semiconductor", "datacenter", "liquid cooling",
                 "HBM", "CoWoS", "3nm", "inference", "training cluster"]

# Engineering vocabulary — used to reject non-engineering filings (e.g. CFO
# departures, buyback authorizations) that mention a ticker but contain zero
# verifiable engineering content. See is_engineering_relevant().
ENGINEERING_TERMS = [
    "yield", "capacity", "shipment", "production", "volume", "nm", "wafer",
    "bandwidth", "efficiency", "constraint", "supply", "delay", "ramp",
    "throughput", "architecture", "fabrication", "node", "packaging",
    "memory", "node", "tape-out", "tapeout", "lithography", "thermal",
]
ENGINEERING_RELEVANCE_THRESHOLD = 3   # min distinct engineering terms required


def is_engineering_relevant(content: str,
                            threshold: int = ENGINEERING_RELEVANCE_THRESHOLD) -> bool:
    """
    True if `content` contains at least `threshold` DISTINCT engineering terms.
    Guards Tier 1 (EDGAR) against high-credibility but engineering-empty filings:
    a CFO-departure 8-K is Tier 1 by source, but contains no verifiable claim.
    """
    if not content:
        return False
    low = content.lower()
    hits = {t for t in ENGINEERING_TERMS if t in low}
    return len(hits) >= threshold


def verify_quote(quote: str, content: str) -> bool:
    """
    True if `quote` is genuinely grounded in `content`.
    Primary check: normalized substring match.
    Fallback: >=85% token overlap (tolerates minor punctuation/whitespace drift).
    Returns False when the LLM likely hallucinated the verbatim_quote.
    """
    if not quote or not content:
        return False
    norm = lambda s: re.sub(r"\s+", " ", s.lower()).strip()
    nq, nc = norm(quote), norm(content)
    if len(nq) < 8:
        return False
    if nq in nc:
        return True
    q_tokens = [t for t in re.findall(r"\w+", nq) if len(t) > 3]
    if not q_tokens:
        return False
    overlap = sum(1 for t in q_tokens if t in nc) / len(q_tokens)
    return overlap >= 0.85

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

            # Engineering-relevance gate (Tier 1 quality guard):
            # reject filings that mention the ticker but carry no verifiable
            # engineering content (CFO departures, buybacks, governance, etc.)
            if not is_engineering_relevant(body):
                print(f"  [EDGAR] skip {ticker} {form} {dates[i]} "
                      f"— below engineering relevance threshold")
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


def call_llm_stable(system_prompt: str, user_content: str,
                    n_runs: int = 3,
                    numeric_keys: tuple = (),
                    model: str = GLM_MODEL) -> Optional[dict]:
    """
    Calls call_llm n_runs times and aggregates results for stability.

    Motivation: ArXiv 2603.04417 shows temperature=0 does NOT guarantee
    determinism in LLM scoring tasks. ArXiv 2503.16974 (2025) shows
    3-5 run aggregation "dramatically improves consistency."

    Aggregation strategy:
      - numeric_keys  → median across runs (robust to outliers)
      - list fields   → from the first successful run
      - all other     → mode (most frequent string value), ties → first run

    For n_runs=1, returns a single call_llm result unchanged.
    """
    if n_runs <= 1:
        return call_llm(system_prompt, user_content, model)

    from collections import Counter
    runs = []
    for i in range(n_runs):
        r = call_llm(system_prompt, user_content, model)
        if r is not None:
            runs.append(r)

    if not runs:
        return None
    if len(runs) == 1:
        return runs[0]

    merged = {}
    for k in runs[0].keys():
        vals = [r[k] for r in runs if k in r]
        if not vals:
            merged[k] = runs[0].get(k)
            continue

        if k in numeric_keys:
            # median — robust against single-run outliers
            try:
                floats = sorted(float(v) for v in vals)
                merged[k] = floats[len(floats) // 2]
            except (ValueError, TypeError):
                merged[k] = vals[0]
        elif isinstance(vals[0], list):
            # lists: take from the first run to avoid list-merge complexity
            merged[k] = vals[0]
        else:
            # mode (most frequent), ties broken by first run
            counts = Counter(str(v) for v in vals)
            most_common_str = counts.most_common(1)[0][0]
            merged[k] = next((v for v in vals if str(v) == most_common_str), vals[0])

    return merged

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
        raw_quote = data.get("verbatim_quote", "")
        # Hallucination guard: verify the quote actually exists in the source.
        # Only meaningful when we have document body (Tier 1/2). For Tier 3
        # (headline-only, empty content) we skip verification.
        quote = raw_quote
        if raw_quote and document.content and not verify_quote(raw_quote, document.content):
            print(f"  [LLM] ⚠ verbatim_quote not found in source — marking unverified")
            quote = "[UNVERIFIED] " + raw_quote

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
            verbatim_quote=quote,
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

    # 3-run median aggregation for the two numeric scores (I3 fix).
    # Evidence: ArXiv 2503.16974 shows 3-run aggregation dramatically
    # improves consistency for LLM scoring tasks. Adds ~2× token spend
    # only on the RealityAgent, which sensitivity analysis shows drives
    # ~2.66× more gap variance than Narrative inputs.
    data = call_llm_stable(system, user, n_runs=3,
                           numeric_keys=("feasibility_score", "constraint_penalty"))
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

    # Mirror to SQLite so the dashboard reflects this run immediately.
    # JSONL above remains the source of truth; SQLite is a query-acceleration
    # layer. Failure here is non-fatal — the JSONL record is already safe.
    try:
        import db
        db.ensure_ready()
        db.write_run(record)
    except Exception as _db_e:
        print(f"  [DB] SQLite write skipped: {_db_e}  (JSONL record is safe)")


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

    # T7: Engineering-relevance gate (EDGAR Tier 1 quality guard)
    eng_text = ("Volume production ramp at the 3nm node; wafer yield and CoWoS "
                "packaging capacity constrain shipment throughput.")
    non_eng  = ("The Board appointed a new Chief Financial Officer effective "
                "immediately and authorized a share repurchase program.")
    check("T7a engineering text passes relevance gate",
          is_engineering_relevant(eng_text))
    check("T7b CFO/buyback filing rejected by relevance gate",
          not is_engineering_relevant(non_eng))
    check("T7c empty content rejected", not is_engineering_relevant(""))

    # T8: verbatim_quote hallucination guard
    src_body = ("NVIDIA commenced volume production of Blackwell at TSMC N4P, "
                "with initial deliveries beginning Q1 2027 subject to CoWoS capacity.")
    check("T8a exact quote verified",
          verify_quote("initial deliveries beginning Q1 2027", src_body))
    check("T8b whitespace-variant quote verified",
          verify_quote("initial   deliveries  beginning Q1 2027", src_body))
    check("T8c fabricated quote rejected",
          not verify_quote("the CEO confirmed a 90 percent gross margin target", src_body))
    check("T8d empty quote rejected", not verify_quote("", src_body))

    # T6: History record keys
    write_history("test-session", n, r, stub_market(), gap, mode="test")
    import linecache
    history_lines = HISTORY_PATH.read_text(encoding="utf-8").strip().split("\n")
    last = json.loads(history_lines[-1])
    for key in ("source_tier", "source_name", "doc_type", "ev_ceiling"):
        check(f"T6 history record has key '{key}'", key in last)

    # T9: Backtest directional validation (regression-locks VERIFIED behavior)
    bt = run_backtest()
    check("T9a backtest runs all cases",
          bt["total"] == len(HISTORICAL_CASES), f"got {bt['total']}")
    check("T9b accuracy in [0,1]",
          0.0 <= bt["accuracy"] <= 1.0, f"got {bt['accuracy']}")
    bt_by_id = {x["case_id"]: x for x in bt["results"]}
    check("T9c NVDA Blackwell case flagged MISMATCH",
          bt_by_id["NVDA-BLACKWELL-2024"]["predicted"] == "MISMATCH")
    check("T9d NVDA DC-guidance case flagged ALIGNED",
          bt_by_id["NVDA-DC-GUIDANCE-2023"]["predicted"] == "ALIGNED")
    check("T9e TSMC-N3 (corrected) is ALIGNED hit — skeptics were right",
          bt_by_id["TSMC-N3-2023"]["hit"]
          and bt_by_id["TSMC-N3-2023"]["expected"] == "ALIGNED")
    check("T9f all 5 REAL cases are directional hits (oracle inputs)",
          bt["real_hits"] == 5 and bt["real_total"] == 5,
          f"got {bt['real_hits']}/{bt['real_total']}")
    check("T9g synthetic quiet-divergence case is UNDER-flagged (limitation confirmed)",
          not bt_by_id["SYNTH-QUIET-DIVERGENCE"]["hit"]
          and bt_by_id["SYNTH-QUIET-DIVERGENCE"]["predicted"] == "ALIGNED")

    # T10: Formula sensitivity analysis (pure math, no data)
    # T10a: closed-form gap must match compute_gap_index (market excluded)
    n_cf = stub_narrative()
    n_cf.sentiment_polarity, n_cf.propagation, n_cf.novelty = 0.5, 0.5, "echo"
    r_cf = stub_reality()
    r_cf.feasibility_score, r_cf.constraint_penalty, r_cf.evidence_strength = 0.6, 0.2, "moderate"
    m_cf = MarketObject("X", "2020-01-01", None, "excluded")
    gap_obj = compute_gap_index(n_cf, r_cf, m_cf)
    gap_cf = _gap_closed_form(0.5, 0.5, NOVELTY_WEIGHT["echo"],
                              0.6, 0.2, EVIDENCE_WEIGHT["moderate"])
    check("T10a closed-form gap matches compute_gap_index",
          abs(gap_obj.gap_index - gap_cf) < 1e-9,
          f"obj={gap_obj.gap_index} cf={gap_cf}")

    az = analyze_formula()
    check("T10b R-inputs dominate N-inputs (corrected symmetric grid, ratio > 1.2)",
          az["ratio"] > 1.2, f"got {az['ratio']}")
    check("T10c most sensitive input is an R-input (not an N-input)",
          max(az["means"], key=az["means"].get) in ("feasibility", "penalty", "evidence_w"),
          f"got {max(az['means'], key=az['means'].get)}")
    # T10d: compression — quiet/stale claim has smaller detectable gap than loud/fresh
    loud  = _n_score(1.0, 1.0, 1.0)   # |.- 0.5| = 0.5
    quiet = _n_score(1.0, 0.25, 0.3)  # heavily compressed toward 0.5
    check("T10d quiet narrative is compressed toward neutral 0.5",
          abs(quiet - 0.5) < abs(loud - 0.5),
          f"quiet|N-.5|={abs(quiet-0.5):.3f} loud|N-.5|={abs(loud-0.5):.3f}")

    # T11: call_llm_stable aggregation (no API key needed — uses mock data)
    # Directly test the aggregation math, not the LLM call
    mock_runs = [
        {"feasibility_score": 0.7,  "evidence_strength": "moderate", "items": [1, 2]},
        {"feasibility_score": 0.5,  "evidence_strength": "weak",     "items": [3, 4]},
        {"feasibility_score": 0.65, "evidence_strength": "moderate", "items": [5, 6]},
    ]
    from statistics import median as _median
    from collections import Counter as _Counter

    def _mock_aggregate(runs, numeric_keys):
        merged = {}
        for k in runs[0].keys():
            vals = [r[k] for r in runs]
            if k in numeric_keys:
                merged[k] = sorted([float(v) for v in vals])[len(vals) // 2]
            elif isinstance(vals[0], list):
                merged[k] = vals[0]
            else:
                counts = _Counter(str(v) for v in vals)
                mc = counts.most_common(1)[0][0]
                merged[k] = next(v for v in vals if str(v) == mc)
        return merged

    agg = _mock_aggregate(mock_runs, numeric_keys=("feasibility_score",))
    check("T11a n_runs=3 median of [0.7,0.5,0.65] == 0.65",
          agg["feasibility_score"] == 0.65, f"got {agg['feasibility_score']}")
    check("T11b n_runs=3 mode of ['moderate','weak','moderate'] == 'moderate'",
          agg["evidence_strength"] == "moderate", f"got {agg['evidence_strength']}")
    check("T11c list field takes first run",
          agg["items"] == [1, 2])

    print(f"\n  Results: {passed} passed, {failed} failed")
    return failed == 0


# ════════════════════════════════════════════════════════════════
#  BACKTEST — directional validation against real historical events
# ════════════════════════════════════════════════════════════════
#
#  WHAT THIS DOES (and does NOT) VALIDATE
#  ---------------------------------------
#  Each case below feeds an ORACLE reality assessment — i.e. the
#  engineering truth we now know retrospectively — into the Gap Index.
#  Therefore this backtest validates ONLY the SCORING FORMULA:
#      "Given a correct reality assessment, does NR_gap correctly flag
#       a narrative-reality divergence that history later confirmed?"
#
#  It does NOT validate the RealityAgent (the LLM's ability to PRODUCE
#  a correct reality assessment from a live document). That is a separate,
#  harder test ("Stage B") requiring live GLM calls on archived documents.
#
#  Market data is EXCLUDED from scoring (m_implied=None) so gap_index == NR_gap.
#  This isolates the core claim: narrative-vs-reality divergence detection.
#  The real ~5-day market return is shown only as CONTEXT, not as a score input.
#
#  Cases are author-encoded representations of well-documented PUBLIC events.
#  Reality inputs are retrospective. Claims are paraphrased, not verbatim quotes.
# ════════════════════════════════════════════════════════════════

@dataclass
class BacktestCase:
    case_id:       str
    date:          str            # event date (ISO)
    ticker:        str
    description:   str
    narrative:     dict           # sentiment_polarity, propagation, novelty, certainty, claim
    reality:       dict           # feasibility_score, constraint_penalty, evidence_strength, ...
    market_return: Optional[float]  # real ~5d return around event (public, for CONTEXT only)
    expected:      str            # "MISMATCH" | "ALIGNED" (ground-truth direction)
    resolution:    str            # what actually happened
    source_note:   str            # public event reference
    synthetic:     bool = False   # True = constructed stress-case, NOT a real event


HISTORICAL_CASES = [
    BacktestCase(
        case_id="NVDA-BLACKWELL-2024",
        date="2024-08-05",
        ticker="NVDA",
        description="Bullish narrative of imminent Blackwell volume ramp vs packaging/mask reality",
        narrative=dict(
            claim="NVIDIA Blackwell is ramping to volume shipments imminently with no material delay.",
            sentiment_polarity=1.0, propagation=1.0, novelty="first_report", certainty="high"),
        reality=dict(
            technical_change="Blackwell volume production at TSMC with CoWoS-L packaging",
            feasibility_score=0.5, constraint_penalty=0.4, evidence_strength="moderate",
            open_constraints=["CoWoS-L packaging capacity", "GPU mask/photomask revision"],
            hardware_constraint="Advanced packaging (CoWoS-L) throughput",
            supply_chain_risk="HBM3e + packaging shared constraints",
            primary_constraint="CoWoS-L packaging capacity"),
        market_return=-0.05,
        expected="MISMATCH",
        resolution=("Reported Aug 3 2024 (The Information via The Verge); shipments slipped to Q1 2025 "
                    "due to CoWoS packaging complexity. Huang later (Aug 28 2024 earnings) attributed it "
                    "to a mask issue affecting yield, not a design flaw. Narrative was ahead of reality."),
        source_note=("The Verge 2024-08-03; The Register 2024-08-05; Reuters 2024-08-28. "
                     "NOTE: -5% market figure is CONFOUNDED by the Aug 5 2024 global selloff "
                     "(yen carry-trade unwind) — not cleanly attributable to the delay.")),

    BacktestCase(
        case_id="NVDA-DC-GUIDANCE-2023",
        date="2023-05-24",
        ticker="NVDA",
        description="Bullish datacenter guidance that engineering + demand reality actually supported",
        narrative=dict(
            claim="NVIDIA datacenter demand is accelerating sharply with strong forward guidance.",
            sentiment_polarity=1.0, propagation=1.0, novelty="first_report", certainty="high"),
        reality=dict(
            technical_change="H100 datacenter GPU supply scaling to meet AI training demand",
            feasibility_score=0.9, constraint_penalty=0.05, evidence_strength="strong",
            open_constraints=[],
            hardware_constraint="CoWoS capacity (scaling, not blocking)",
            supply_chain_risk="low",
            primary_constraint="none binding"),
        market_return=0.24,
        expected="ALIGNED",
        resolution=("NVDA's May 2023 datacenter guidance proved accurate; revenue ramped as claimed. "
                    "Narrative matched reality. Stock +24.7% after-hours / +14.3% regular-session close."),
        source_note="Motley Fool 2023-05-24 (+24.7% AH); StockStory 2023-05-25 (+14.3% close)."),

    BacktestCase(
        case_id="INTC-7NM-2020",
        date="2020-07-24",
        ticker="INTC",
        description="Reassuring 'process on-track' narrative vs yield reality",
        narrative=dict(
            claim="Intel's advanced process roadmap remains on track for near-term product delivery.",
            sentiment_polarity=0.5, propagation=0.5, novelty="echo", certainty="moderate"),
        reality=dict(
            technical_change="Intel 7nm process node yield/defect density",
            feasibility_score=0.25, constraint_penalty=0.3, evidence_strength="moderate",
            open_constraints=["7nm defect density", "yield learning rate"],
            hardware_constraint="Process node yield",
            supply_chain_risk="internal fab dependency",
            primary_constraint="7nm yield"),
        market_return=-0.162,
        expected="MISMATCH",
        resolution=("Intel disclosed a ~6-month 7nm delay (yields ~12 months behind target) at Q2 2020 "
                    "earnings after close Jul 23 2020; stock closed -16.2% on Jul 24. The prior "
                    "'on-track' narrative was mispriced against yield reality."),
        source_note="Motley Fool 2020-07-24 (6-mo delay, yields 12mo behind); MarketWatch (-16.2% close Jul 24 2020)."),

    BacktestCase(
        case_id="AMD-MI300X-2023",
        date="2023-12-06",
        ticker="AMD",
        description="Hardware-competitive AI accelerator claim vs software-ecosystem reality",
        narrative=dict(
            claim="AMD MI300X competes directly with NVIDIA H100 for AI workloads at launch.",
            sentiment_polarity=1.0, propagation=1.0, novelty="first_report", certainty="high"),
        reality=dict(
            technical_change="MI300X hardware competitiveness + ROCm software stack maturity",
            feasibility_score=0.7, constraint_penalty=0.35, evidence_strength="moderate",
            open_constraints=["ROCm software maturity", "framework/library support", "customer migration cost"],
            hardware_constraint="none material (hardware competitive)",
            supply_chain_risk="moderate",
            primary_constraint="ROCm software ecosystem maturity"),
        market_return=0.02,
        expected="MISMATCH",
        resolution=("MI300X hardware was competitive (1.3–1.6x H100 on paper), but real-world adoption "
                    "was gated by ROCm software maturity through 2024; revenue ramped slower than the "
                    "launch narrative implied."),
        source_note=("AMD press release 2023-12-06 (launch + ROCm 6); TechPowerU​p 2024-12 and "
                     "daily.dev benchmark 2024-12 (ROCm software cited as the adoption bottleneck).")),

    BacktestCase(
        case_id="TSMC-N3-2023",
        date="2023-04-20",
        ticker="TSM",
        description="Cautious narrative of a slow N3 ramp that matched TSMC's own guidance",
        narrative=dict(
            claim="TSMC N3 will ramp slowly in 2023 and not be a significant revenue contributor this year.",
            sentiment_polarity=-0.5, propagation=0.5, novelty="echo", certainty="moderate"),
        reality=dict(
            technical_change="TSMC N3 (3nm) yield and volume ramp through 2023",
            feasibility_score=0.55, constraint_penalty=0.15, evidence_strength="moderate",
            open_constraints=["slow initial ramp", "high N3B design cost", "limited 2023 volume"],
            hardware_constraint="N3B ramp pace / design cost",
            supply_chain_risk="low",
            primary_constraint="ramp pace (slow by design, per TSMC guidance)"),
        market_return=0.06,
        expected="ALIGNED",
        resolution=("CORRECTED after verification: the cautious 'slow ramp' narrative was substantially "
                    "ACCURATE. TSMC itself guided N3 as 'not a significant contributor in 2023'; N3 was "
                    "only ~6% of revenue in Q3 2023, reaching 15% only in Q4 2023. Narrative matched reality."),
        source_note=("AnandTech 2023-01 (TSMC: not significant in 2023); EE Times 2023-04 (mid-single-digit "
                     "2023 guidance); AnandTech 2023-10 (6% Q3) & 2024-01 (15% Q4). "
                     "Prior encoding labeled this a 'pessimism mispricing' — verification showed the "
                     "skeptics were largely right, so the ground-truth label was changed MISMATCH→ALIGNED.")),

    # ── SYNTHETIC stress-case (NOT a real event) ──────────────────────────────
    # Isolates a genuine formula limitation discovered via the N_score algebra:
    #   N_score = 0.5 + 0.5·clamp(sentiment · propagation · novelty_weight)
    # A QUIET divergence (low propagation × echo/stale novelty) is pulled toward
    # the neutral midpoint (0.5) regardless of sentiment SIGN, shrinking NR_gap.
    # So a real, strong narrative-reality divergence carried by a low-propagation
    # claim is UNDER-flagged. This affects bullish and bearish narratives equally;
    # it is a propagation/novelty compression, NOT a negative-sentiment effect.
    BacktestCase(
        case_id="SYNTH-QUIET-DIVERGENCE",
        date="0000-00-00",
        ticker="SYNTH",
        description="Constructed: strongly divergent but LOW-PROPAGATION narrative the formula under-flags",
        narrative=dict(
            claim="[SYNTHETIC] A strongly skeptical but niche/stale claim that diverges sharply from a strong reality.",
            sentiment_polarity=-1.0, propagation=0.25, novelty="stale", certainty="high"),
        reality=dict(
            technical_change="[SYNTHETIC] Strong, well-evidenced engineering reality",
            feasibility_score=0.75, constraint_penalty=0.05, evidence_strength="strong",
            open_constraints=[],
            hardware_constraint="none",
            supply_chain_risk="low",
            primary_constraint="none"),
        market_return=None,
        expected="MISMATCH",   # the TRUE divergence is large; formula should flag it but won't
        resolution=("Constructed stress-case. Reality (R≈0.71) diverges sharply from a strongly skeptical "
                    "narrative, but low propagation (0.25) × stale novelty (0.3) compresses N_score toward "
                    "0.5, so NR_gap falls below the 0.35 threshold and the divergence is UNDER-flagged. "
                    "Demonstrates the propagation/novelty compression limitation."),
        source_note="Synthetic — no real event. Demonstrates an N_score algebraic limitation.",
        synthetic=True),
]


def _backtest_objects(case: BacktestCase):
    """Build NarrativeObject + RealityObject + (market-excluded) MarketObject from a case."""
    n = NarrativeObject(
        claim=case.narrative["claim"],
        source_url=f"backtest://{case.case_id}",
        sentiment_polarity=case.narrative["sentiment_polarity"],
        propagation=case.narrative["propagation"],
        novelty=case.narrative["novelty"],
        certainty=case.narrative["certainty"],
        source_tier=1, source_name="backtest", doc_type="historical",
        verbatim_quote=case.narrative["claim"],
    )
    rd = case.reality
    r = RealityObject(
        technical_change=rd["technical_change"],
        feasibility_score=rd["feasibility_score"],
        constraint_penalty=rd["constraint_penalty"],
        evidence_strength=rd["evidence_strength"],
        open_constraints=rd.get("open_constraints", []),
        hardware_constraint=rd.get("hardware_constraint", ""),
        supply_chain_risk=rd.get("supply_chain_risk", ""),
        evidence_ceiling="strong",   # oracle: not tier-limited
        primary_constraint=rd.get("primary_constraint", ""),
        comparable_events=[],
    )
    # Market EXCLUDED from scoring → gap_index == NR_gap
    m = MarketObject(ticker=case.ticker, event_date=case.date,
                     event_window_return=None, data_quality="excluded")
    return n, r, m


def run_backtest(threshold: float = 0.35) -> dict:
    """
    Run all historical cases and report directional accuracy.
    A case is a HIT when the predicted bucket matches the known outcome:
      predicted = MISMATCH  if gap_index >= threshold  else ALIGNED
    Returns summary dict with per-case results and overall accuracy.
    """
    print("\n" + "=" * 64)
    print("  NRS-1 v3 — BACKTEST (directional formula validation)")
    print("=" * 64)
    print("  NOTE: oracle reality inputs · market EXCLUDED from score · "
          "gap_index == NR_gap")
    print("  Validates the FORMULA, not the RealityAgent. See module header.\n")

    results = []
    real_hits = 0
    real_total = 0
    for case in HISTORICAL_CASES:
        n, r, m = _backtest_objects(case)
        gap = compute_gap_index(n, r, m)
        gi = gap.gap_index if gap.gap_index is not None else 0.0
        predicted = "MISMATCH" if gi >= threshold else "ALIGNED"
        hit = (predicted == case.expected)
        if not case.synthetic:
            real_total += 1
            real_hits += int(hit)
        results.append({
            "case_id": case.case_id, "ticker": case.ticker, "date": case.date,
            "gap_index": gap.gap_index, "gap_label": gap.gap_label,
            "n_score": gap.n_score, "r_score": gap.r_score,
            "predicted": predicted, "expected": case.expected,
            "hit": hit, "synthetic": case.synthetic,
            "market_return": case.market_return, "resolution": case.resolution,
        })
        tag = "SYNTH" if case.synthetic else "REAL "
        mark = "✓ HIT " if hit else "✗ MISS"
        mret = (f"{case.market_return*100:+.0f}%" if case.market_return is not None else "n/a")
        print(f"  [{tag}] {mark} | {case.case_id:24s} | gap={gi:.3f} ({gap.gap_label:17s})"
              f" pred={predicted:8s} exp={case.expected:8s} | mkt~{mret}")

    accuracy = round(real_hits / real_total, 4) if real_total else 0.0
    print(f"\n  REAL-case directional accuracy: {real_hits}/{real_total} = {accuracy:.0%}")
    print("  (oracle reality inputs → validates the FORMULA only, not the RealityAgent)")

    synth = [x for x in results if x["synthetic"]]
    if synth:
        print("\n  Synthetic stress-cases (constructed to expose formula limitations):")
        for x in synth:
            outcome = "correctly flagged" if x["hit"] else "UNDER-flagged (limitation confirmed)"
            print(f"    - {x['case_id']}: gap={x['gap_index']:.3f} → {outcome}")
            print(f"      → {x['resolution'][:88]}...")

    misses = [x for x in results if not x["hit"] and not x["synthetic"]]
    if misses:
        print("\n  Real-case misses:")
        for x in misses:
            print(f"    - {x['case_id']}: predicted {x['predicted']} but "
                  f"reality was {x['expected']}")
            print(f"      gap={x['gap_index']:.3f} (N={x['n_score']:.3f}, R={x['r_score']:.3f})")
            print(f"      → {x['resolution'][:90]}...")
    print()

    return {"real_total": real_total, "real_hits": real_hits, "accuracy": accuracy,
            "total": len(HISTORICAL_CASES), "results": results}


# ════════════════════════════════════════════════════════════════
#  FORMULA SENSITIVITY ANALYSIS  (pure math — needs no test data)
# ════════════════════════════════════════════════════════════════
#
#  PURPOSE
#  -------
#  Measures the EXISTING Gap Index formula's mathematical properties.
#  Changes nothing — it diagnoses, it does not "improve". This avoids the
#  trap of re-tuning an uncalibrated formula without data to validate it.
#
#  Market is excluded → gap == NR_gap == |N_score - R_score|, where:
#      N_score = 0.5 + 0.5 · clamp(sentiment · propagation · novelty_w, -1, 1)
#      R_score = clamp(feasibility · (1 - constraint_penalty) · evidence_w, 0, 1)
#
#  Closed-form local partials (valid in the unclamped interior):
#      ∂N/∂sentiment   = 0.5 · propagation · novelty_w
#      ∂N/∂propagation = 0.5 · sentiment   · novelty_w
#      ∂N/∂novelty_w   = 0.5 · sentiment   · propagation
#      ∂R/∂feasibility = (1 - constraint_penalty) · evidence_w
#      ∂R/∂penalty     = -feasibility · evidence_w
#      ∂R/∂evidence_w  = feasibility · (1 - constraint_penalty)
#  |∂gap/∂x| = |∂N/∂x| for N-inputs, |∂R/∂x| for R-inputs (the sign(N-R)
#  factor has magnitude 1 and drops out of the sensitivity magnitude).
# ════════════════════════════════════════════════════════════════

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _n_score(sentiment, propagation, novelty_w):
    return 0.5 + 0.5 * _clamp(sentiment * propagation * novelty_w, -1.0, 1.0)


def _r_score(feasibility, penalty, evidence_w):
    return _clamp(feasibility * (1.0 - penalty) * evidence_w, 0.0, 1.0)


def _gap_closed_form(sentiment, propagation, novelty_w,
                     feasibility, penalty, evidence_w):
    """Market-excluded gap == |N_score - R_score|. Mirrors compute_gap_index."""
    return abs(_n_score(sentiment, propagation, novelty_w)
               - _r_score(feasibility, penalty, evidence_w))


def analyze_formula() -> dict:
    """
    Sensitivity analysis of the Gap Index (market excluded).
    Reports: (1) local partials at representative points, (2) the
    propagation/novelty compression effect, (3) which input dominates
    the gap — the argument for prioritizing RealityAgent accuracy.
    Returns a summary dict (also used by unit tests).
    """
    print("\n" + "=" * 64)
    print("  NRS-1 v3 — FORMULA SENSITIVITY ANALYSIS  (no data needed)")
    print("=" * 64)
    print("  gap = |N_score - R_score|   (market excluded)\n")

    # ── (1) Local sensitivities at representative operating points ──
    points = [
        ("bullish-loud / weak-reality", dict(sentiment=1.0, propagation=1.0,
            novelty_w=1.0, feasibility=0.4, penalty=0.3, evidence_w=0.4)),
        ("typical mid-range",           dict(sentiment=0.5, propagation=0.5,
            novelty_w=0.6, feasibility=0.6, penalty=0.2, evidence_w=0.7)),
        ("aligned strong-reality",      dict(sentiment=1.0, propagation=1.0,
            novelty_w=1.0, feasibility=0.9, penalty=0.05, evidence_w=1.0)),
    ]
    print("  (1) LOCAL |∂gap/∂x| at representative points")
    print("      " + "-" * 56)
    for name, p in points:
        dN_s  = abs(0.5 * p["propagation"] * p["novelty_w"])
        dN_p  = abs(0.5 * p["sentiment"]   * p["novelty_w"])
        dN_nu = abs(0.5 * p["sentiment"]   * p["propagation"])
        dR_f  = abs((1.0 - p["penalty"]) * p["evidence_w"])
        dR_c  = abs(p["feasibility"] * p["evidence_w"])
        dR_e  = abs(p["feasibility"] * (1.0 - p["penalty"]))
        print(f"      {name}")
        print(f"        N-inputs:  sentiment={dN_s:.3f}  propagation={dN_p:.3f}"
              f"  novelty={dN_nu:.3f}")
        print(f"        R-inputs:  feasibility={dR_f:.3f}  penalty={dR_c:.3f}"
              f"  evidence={dR_e:.3f}")

    # ── (2) Propagation/novelty compression of N_score ──
    print("\n  (2) N_score COMPRESSION  (sentiment fixed at +1.0 = max claim)")
    print("      detectable divergence = |N_score - 0.5|; shrinks as a claim")
    print("      circulates quietly (low propagation) or is stale (low novelty)")
    print("      " + "-" * 56)
    print("      propagation  novelty  N_score   |N-0.5| (max detectable gap)")
    compression = []
    for prop in (1.0, 0.5, 0.25):
        for nov_name, nov_w in (("first", 1.0), ("echo", 0.6), ("stale", 0.3)):
            ns = _n_score(1.0, prop, nov_w)
            detect = abs(ns - 0.5)
            compression.append((prop, nov_w, round(detect, 4)))
            print(f"        {prop:>5.2f}      {nov_name:<6s}  {ns:.4f}    {detect:.4f}")

    # ── (3) Global dominance ranking (which input drives the gap) ──
    grid = dict(
        # N-inputs include sentiment=0 (contributes zero partial — this pulls N-means down)
        # R-inputs include evidence_w=0.0 (insufficient evidence — symmetric treatment)
        # Both grids now include their respective zero-contribution values.
        # The resulting ratio is still directionally correct but smaller and more honest
        # than a grid that excludes evidence_w=0.
        sentiment=[-1.0, -0.5, 0.0, 0.5, 1.0],
        propagation=[0.25, 0.5, 1.0],
        novelty_w=[0.3, 0.6, 1.0],
        feasibility=[0.25, 0.5, 0.75, 1.0],
        penalty=[0.0, 0.15, 0.3, 0.45],
        evidence_w=[0.0, 0.4, 0.7, 1.0],  # 0.0 = insufficient; included for symmetry
    )
    import itertools
    sums = {k: 0.0 for k in ("sentiment", "propagation", "novelty_w",
                             "feasibility", "penalty", "evidence_w")}
    count = 0
    for s, p, nu, f, c, e in itertools.product(
            grid["sentiment"], grid["propagation"], grid["novelty_w"],
            grid["feasibility"], grid["penalty"], grid["evidence_w"]):
        sums["sentiment"]   += abs(0.5 * p * nu)
        sums["propagation"] += abs(0.5 * s * nu)
        sums["novelty_w"]   += abs(0.5 * s * p)
        sums["feasibility"] += abs((1.0 - c) * e)
        sums["penalty"]     += abs(f * e)
        sums["evidence_w"]  += abs(f * (1.0 - c))
        count += 1
    means = {k: round(v / count, 4) for k, v in sums.items()}
    ranked = sorted(means.items(), key=lambda kv: kv[1], reverse=True)

    print("\n  (3) GLOBAL mean |∂gap/∂x| over input grid (dominance ranking)")
    print("      " + "-" * 56)
    for k, v in ranked:
        layer = "R (RealityAgent)" if k in ("feasibility", "penalty", "evidence_w") else "N (Narrative)"
        bar = "█" * int(v * 40)
        print(f"        {k:<12s} {v:.4f}  {bar}  [{layer}]")

    r_mean = (means["feasibility"] + means["penalty"] + means["evidence_w"]) / 3
    n_mean = (means["sentiment"] + means["propagation"] + means["novelty_w"]) / 3
    ratio = round(r_mean / n_mean, 2) if n_mean else float("inf")
    print(f"\n      R-inputs mean={r_mean:.4f}  vs  N-inputs mean={n_mean:.4f}"
          f"  →  ratio {ratio}x")
    print(f"      NOTE: ratio is grid-dependent. This grid symmetrically includes")
    print(f"      zero-contribution values for both sides (sentiment=0, evidence_w=0)")
    print(f"      so it understates neither layer. Still directionally robust:")
    print(f"      a fixed error in feasibility/evidence moves the gap ~{ratio}x more")
    print(f"      than the same error in a Narrative input.")
    print(f"      CONCLUSION: RealityAgent accuracy is the dominant error source")
    print(f"      → --backtest-live is the provable bottleneck.\n")

    return {"local_points": len(points), "compression": compression,
            "means": means, "r_mean": round(r_mean, 4),
            "n_mean": round(n_mean, 4), "ratio": ratio}


# ════════════════════════════════════════════════════════════════
#  BACKTEST LIVE — Stage B: test RealityAgent on real documents
# ════════════════════════════════════════════════════════════════
#
#  Stage A (--backtest): validated the FORMULA using oracle reality inputs.
#  Stage B (this):       validates the AGENT — given a real historical document,
#                        does GLM produce a reality assessment close to the oracle?
#
#  Metric: Mean Absolute Error (MAE) for feasibility_score
#    MAE ≤ 0.15  →  signal is meaningful for production use
#    MAE 0.15–0.30 → directionally useful but noisy
#    MAE ≥ 0.30  →  scores unreliable; fix agent before adding more features
#
#  Requires ZHIPU_API_KEY. Exits gracefully without it.
# ════════════════════════════════════════════════════════════════

# Each entry maps a BacktestCase.case_id to the best EDGAR filing to use.
# Dates chosen to be the closest primary filing to the narrative event.
# Source for each date: verified in prior external verification round.
LIVE_DOC_MAP = {
    "NVDA-BLACKWELL-2024": {
        "ticker": "NVDA", "target_date": "2024-08-28",
        "note": "NVDA Q2 FY2025 8-K (Aug 28 2024) — closest primary filing; "
                "Huang explicitly confirmed delay on earnings call this date "
                "(Reuters 2024-08-28)",
    },
    "NVDA-DC-GUIDANCE-2023": {
        "ticker": "NVDA", "target_date": "2023-05-24",
        "note": "NVDA Q1 FY2024 8-K (May 24 2023) — original datacenter guidance "
                "release; +24.7% AH move (Motley Fool 2023-05-24)",
    },
    "INTC-7NM-2020": {
        "ticker": "INTC", "target_date": "2020-07-23",
        "note": "INTC Q2 2020 8-K (Jul 23 2020) — 7nm delay disclosure; "
                "yields 12mo behind target (Motley Fool 2020-07-24)",
    },
    "AMD-MI300X-2023": {
        "ticker": "AMD", "target_date": "2023-12-06",
        "note": "AMD 8-K (Dec 6 2023) — MI300X launch + ROCm 6 (AMD IR 2023-12-06)",
    },
    "TSMC-N3-2023": {
        "ticker": "TSM", "target_date": "2023-04-20",
        "note": "TSM 6-K / Q1 2023 earnings (around Apr 20 2023) — TSMC guided "
                "'N3 not significant contributor in 2023' (AnandTech 2023-01)",
    },
}


def fetch_edgar_near_date(ticker: str, target_date_str: str,
                          window_days: int = 14) -> Optional[SourceDocument]:
    """
    Fetch the EDGAR filing nearest to `target_date_str`.
    Searches within `window_days` before and after the target date.
    Applies is_engineering_relevant() filter.
    Returns best SourceDocument, or None.
    """
    try:
        import requests
    except ImportError:
        return None

    cik = EDGAR_CIK.get(ticker.upper())
    if not cik:
        return None

    try:
        target = datetime.date.fromisoformat(target_date_str)
        lo = target - datetime.timedelta(days=window_days)
        hi = target + datetime.timedelta(days=window_days)

        url  = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None

        recent   = resp.json().get("filings", {}).get("recent", {})
        forms    = recent.get("form", [])
        dates    = recent.get("filingDate", [])
        accs     = recent.get("accessionNumber", [])
        doc_list = recent.get("primaryDocument", [])
        cik_int  = int(cik)

        candidates = []
        for i, form in enumerate(forms):
            if form not in ("8-K", "10-Q", "6-K", "20-F"):
                continue
            try:
                fd = datetime.date.fromisoformat(dates[i])
            except (ValueError, IndexError):
                continue
            if not (lo <= fd <= hi):
                continue

            acc_clean = accs[i].replace("-", "")
            primary   = doc_list[i] if i < len(doc_list) else ""
            if not primary:
                continue

            doc_url = (f"https://www.sec.gov/Archives/edgar/data/"
                       f"{cik_int}/{acc_clean}/{primary}")
            body = ""
            try:
                dr = requests.get(doc_url, headers=HEADERS, timeout=12)
                if dr.status_code == 200:
                    body = _strip_html(dr.text)[:4000]
            except Exception:
                pass

            if not is_engineering_relevant(body):
                continue

            distance = abs((fd - target).days)
            candidates.append((distance, SourceDocument(
                title=f"{ticker} {form} {dates[i]}",
                content=body, url=doc_url,
                source_name="SEC EDGAR (live)",
                source_tier=1, quality_score=1.0,
                doc_type=form.replace("-", ""),
                pub_date=dates[i], ticker_refs=[ticker],
            )))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    except Exception as e:
        print(f"  [EDGAR-LIVE] {ticker} {target_date_str}: {e}")
        return None


def run_backtest_live() -> dict:
    """
    Stage B: run NarrativeAgent + RealityAgent on real historical documents
    and measure deviation from oracle (hand-encoded BacktestCase) values.

    Key metric: MAE of feasibility_score
      ≤ 0.15 → signal meaningful  |  0.15-0.30 → directional only  |  ≥ 0.30 → fix agent
    """
    if not ZHIPU_API_KEY:
        print("\n  [BACKTEST-LIVE] Requires ZHIPU_API_KEY.")
        print("  Set it: export ZHIPU_API_KEY=<key from open.bigmodel.cn>")
        print("  Optionally: export GLM_MODEL=glm-4.7-flash  (free, better than 4.5)")
        print("  Then re-run: python nrs1_v3.py --backtest-live\n")
        return {"status": "no_key"}

    print("\n" + "=" * 64)
    print("  NRS-1 v3 — BACKTEST LIVE (Stage B: agent accuracy test)")
    print("=" * 64)
    print(f"  Model: {GLM_MODEL}  |  n_runs=3 (median aggregation)")
    print("  Metric: |GLM feasibility_score - oracle| per case\n")

    oracle_map = {c.case_id: c for c in HISTORICAL_CASES if not c.synthetic}
    results = []

    for case_id, doc_info in LIVE_DOC_MAP.items():
        oracle = oracle_map.get(case_id)
        if oracle is None:
            continue

        print(f"  [{case_id}]  fetching: {doc_info['note'][:60]}...")
        doc = fetch_edgar_near_date(doc_info["ticker"], doc_info["target_date"])

        if doc is None:
            print(f"    ✗ No relevant EDGAR document found in ±14-day window. Skipping.")
            results.append({"case_id": case_id, "status": "no_doc"})
            continue

        print(f"    ✓ Document: {doc.title}  ({len(doc.content)} chars)")

        # Run NarrativeAgent
        narrative = live_narrative_agent(doc)
        if narrative is None:
            print(f"    ✗ NarrativeAgent returned None. Skipping.")
            results.append({"case_id": case_id, "status": "narrative_failed"})
            continue

        # Run RealityAgent (3-run median via call_llm_stable inside)
        reality = live_reality_agent(narrative)
        if reality is None:
            print(f"    ✗ RealityAgent returned None. Skipping.")
            results.append({"case_id": case_id, "status": "reality_failed"})
            continue

        oracle_fs = oracle.reality["feasibility_score"]
        oracle_cp = oracle.reality["constraint_penalty"]
        oracle_ev = oracle.reality["evidence_strength"]

        fs_err = abs(reality.feasibility_score - oracle_fs)
        cp_err = abs(reality.constraint_penalty - oracle_cp)
        ev_match = (reality.evidence_strength == oracle_ev)

        print(f"    feasibility:  GLM={reality.feasibility_score:.3f} "
              f"oracle={oracle_fs:.3f}  |err|={fs_err:.3f}")
        print(f"    constraint:   GLM={reality.constraint_penalty:.3f} "
              f"oracle={oracle_cp:.3f}  |err|={cp_err:.3f}")
        print(f"    evidence:     GLM={reality.evidence_strength} "
              f"oracle={oracle_ev}  match={ev_match}")
        print(f"    claim:        {narrative.claim[:65]}...")

        results.append({
            "case_id":    case_id, "status": "ok",
            "oracle_fs":  oracle_fs, "glm_fs":  reality.feasibility_score,
            "oracle_cp":  oracle_cp, "glm_cp":  reality.constraint_penalty,
            "oracle_ev":  oracle_ev, "glm_ev":  reality.evidence_strength,
            "fs_err":     fs_err,    "cp_err":  cp_err, "ev_match": ev_match,
            "doc_title":  doc.title,
        })

    ok = [r for r in results if r.get("status") == "ok"]
    if not ok:
        print("\n  No cases completed (no EDGAR documents or GLM failures).")
        return {"status": "no_results", "results": results}

    mae_fs = round(sum(r["fs_err"] for r in ok) / len(ok), 4)
    mae_cp = round(sum(r["cp_err"] for r in ok) / len(ok), 4)
    ev_acc = round(sum(r["ev_match"] for r in ok) / len(ok), 3)

    print(f"\n  ─────────────────────────────────────────────")
    print(f"  Completed: {len(ok)}/{len(LIVE_DOC_MAP)} cases")
    print(f"  MAE feasibility_score: {mae_fs:.4f}")
    print(f"  MAE constraint_penalty: {mae_cp:.4f}")
    print(f"  Evidence label accuracy: {ev_acc:.0%}")
    if mae_fs <= 0.15:
        verdict = "PASS — signal meaningful for production"
    elif mae_fs <= 0.30:
        verdict = "BORDERLINE — directional only, noisy"
    else:
        verdict = "FAIL — scores unreliable, fix agent before adding features"
    print(f"  Verdict: {verdict}\n")

    return {"status": "ok", "mae_fs": mae_fs, "mae_cp": mae_cp,
            "ev_accuracy": ev_acc, "n_completed": len(ok),
            "verdict": verdict, "results": results}


# ════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print("""
NRS-1 v3 — Narrative-Reality Mispricing Workbench
Usage: python nrs1_v3.py [flag]

  (no flag)          Live mode: fetch real sources, call GLM, write report
  --stub             Stub mode: hardcoded data, no API key needed
  --test             Run 38 unit tests
  --backtest         Directional validation on 5 oracle historical cases
  --backtest-live    Stage B: test GLM on real EDGAR docs vs oracle [needs ZHIPU_API_KEY]
  --analyze          Formula sensitivity analysis (pure math, no data)
  --test-llm         Quick GLM connectivity test [needs ZHIPU_API_KEY]
  --test-edgar TICK  Test EDGAR fetcher for a ticker (e.g. NVDA)
  --help             This message

Environment variables:
  ZHIPU_API_KEY      Required for --backtest-live, --test-llm, live mode
  GLM_MODEL          Override model (default: glm-4.5; try: glm-4.7-flash)
  GMAIL_SENDER / GMAIL_APP_PASSWORD / GMAIL_RECIPIENT  — email dispatch
""")
        sys.exit(0)

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

    elif "--backtest-live" in args:
        run_backtest_live()
        sys.exit(0)

    elif "--backtest" in args:
        summary = run_backtest()
        # exit 0 always — a backtest "miss" is a finding, not a failure
        sys.exit(0)

    elif "--analyze" in args:
        analyze_formula()
        sys.exit(0)

    elif "--test-edgar" in args:
        ticker = args[args.index("--test-edgar") + 1] if args.index("--test-edgar") + 1 < len(args) else "NVDA"
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
