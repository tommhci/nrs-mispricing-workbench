"""
NRS-1 Research Agent (v4 concept)
==================================
Demonstrates the architectural shift: instead of NRS-1 code fetching
documents and passing them to GLM, GLM uses its built-in web_search tool
to autonomously find and evaluate evidence.

This is what you described: AI reads the news, does reasoning, judges whether
a story is real fundamental change vs. narrative mispricing vs. wrong/underselling.

Verified: Z.AI (formerly Zhipu AI) provides Web Search in Chat API.
Source: docs.z.ai/guides/tools/web-search (2025)
Source: GLM-5 supports function calling (milvus.io)

Architecture:
  Input: ticker + topic (e.g. "NVDA", "Blackwell GPU production update")
  GLM autonomously:
    1. Searches for latest news (14 days)
    2. Searches for SEC filing summaries  
    3. Searches for expert technical commentary
    4. Multi-step reasoning: narrative vs. engineering reality
  Output: structured verdict compatible with existing NRS-1 RealityObject

Usage:
  python research_agent.py NVDA "Blackwell GPU production ramp"
  python research_agent.py AMD "MI400 announcement real or hype"
  python research_agent.py TSM "N2 yield progress"

Requires:
  ZHIPU_API_KEY (from open.bigmodel.cn)
  pip install openai>=1.0.0

LIMITATIONS (honest):
  - LLM cannot make precise quantitative predictions
  - Web search may miss paywalled content (SemiAnalysis, earnings transcripts)
  - GLM web_search specific parameter format needs verification against
    current bigmodel.cn docs — may require adjustment
  - Not investment advice. Experimental.

NOT INVESTMENT ADVICE. All outputs experimental and uncalibrated.
"""

import json
import os
import sys
from typing import Optional

ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
GLM_BASE_URL  = "https://open.bigmodel.cn/api/paas/v4/"
# GLM-4.5 supports web_search tool and function calling
# When GLM-5.2 API is available (next week from June 2026):
#   swap to "glm-5.2" for 1M context + better agentic reasoning
GLM_MODEL     = os.environ.get("GLM_MODEL", "glm-4.5")



# ── Verdict types ──────────────────────────────────────────────────────────────
VERDICTS = {
    "REAL_FUNDAMENTAL_CHANGE": (
        "Primary evidence confirms the claim. Engineering timeline is plausible. "
        "Supply chain constraints addressed or minimal."
    ),
    "NARRATIVE_AHEAD_OF_REALITY": (
        "Widely circulated but lacks primary source confirmation. "
        "Engineering timeline is optimistic. Supply chain constraints unresolved."
    ),
    "NARRATIVE_BEHIND_REALITY": (
        "Market/narrative is too pessimistic. Engineering evidence is stronger "
        "than the prevailing story. Potential wrong-way mispricing."
    ),
    "INSUFFICIENT_EVIDENCE": (
        "Could not find sufficient primary evidence to make a determination. "
        "More research required."
    ),
}


# ── System prompt ──────────────────────────────────────────────────────────────
RESEARCH_SYSTEM_PROMPT = """You are an engineering fundamentals analyst specializing
in AI semiconductor and technology companies. Your job is to determine whether a
market narrative is grounded in real engineering and supply chain reality.

You have access to a web search tool. Use it to find:
1. Latest news and announcements (last 14 days) about the topic
2. SEC filings or earnings call excerpts if available
3. Expert technical commentary from sources like SemiAnalysis, Epoch AI, IEEE
4. Historical analogues of similar claims

OUTPUT CONTRACT (critical for unattended operation):
Your ENTIRE response must be a single valid JSON object and NOTHING else.
Do NOT write any reasoning, narration, or ReAct trace outside the JSON.
Your chain of thought goes INSIDE the "reasoning" field below — this keeps the
whole output machine-parseable while preserving your analytical process.

Produce EXACTLY these fields:
{
  "reasoning": "<your step-by-step analysis: what you searched, what you found, how you weighed conflicting evidence. Keep it inside this string field.>",
  "claim": "<the specific claim or narrative being evaluated>",
  "narrative_direction": "bullish|bearish|neutral",
  "evidence_summary": "<2-3 sentences: what did you actually find?>",
  "key_sources": ["<url or source name>", "..."],
  "feasibility_score": <0.0=impossible / 0.25=no clear path / 0.5=plausible /
                         0.75=likely / 1.0=demonstrated>,
  "constraint_penalty": <0.0-0.9, total penalty for unresolved constraints>,
  "evidence_strength": "strong|moderate|weak|insufficient",
  "primary_constraint": "<the single most binding constraint, or 'none'>",
  "comparable_events": ["<historical analogue>"],
  "verdict": "REAL_FUNDAMENTAL_CHANGE|NARRATIVE_AHEAD_OF_REALITY|NARRATIVE_BEHIND_REALITY|INSUFFICIENT_EVIDENCE",
  "verdict_explanation": "<2-3 sentences explaining the verdict>",
  "confidence": "high|moderate|low"
}

CRITICAL RULES:
- Only claim evidence_strength='strong' if you found primary SEC/earnings/
  technical paper sources. Media summaries = 'weak' at best.
- feasibility_score - constraint_penalty must be >= 0
- Return ONLY the JSON object. No markdown fences. No text before or after it.
- Do NOT fabricate sources or quotes. If you did not find a source, do not list it.
- If conflicting evidence cannot be reconciled, state the divergence in "reasoning"
  and lower "confidence" — do NOT invent a resolution.

FAILURE-MODE HANDLING (unattended environment):
- If web search returns no relevant results: set evidence_strength='insufficient',
  verdict='INSUFFICIENT_EVIDENCE', confidence='low'. Do NOT guess.
- If you are uncertain whether search worked: report what you actually have and
  mark evidence_strength conservatively. Never claim certainty you do not have."""



# ── Core research function ─────────────────────────────────────────────────────

def research(ticker: str, topic: str,
             n_runs: int = 1) -> Optional[dict]:
    """
    Main research function. GLM autonomously searches for information
    about ticker/topic and returns a structured verdict.

    Args:
        ticker:  Company ticker, e.g. "NVDA"
        topic:   What to investigate, e.g. "Blackwell GPU production ramp 2026"
        n_runs:  Number of runs for consistency (median for numeric, mode for
                 categorical). Default 1 to conserve tokens.

    Returns:
        dict with verdict and all NRS-1 compatible fields, or None on failure.

    NOTE: The web_search tool parameter format is based on Z.AI documentation
    (docs.z.ai, 2025). If the API returns a tool_call error, check the current
    bigmodel.cn documentation for the exact tool schema — it may differ.
    """
    if not ZHIPU_API_KEY:
        print("[RESEARCH] ZHIPU_API_KEY not set. Export it first.")
        print("  export ZHIPU_API_KEY=<key from open.bigmodel.cn>")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        print("[RESEARCH] openai not installed: pip install openai>=1.0.0")
        return None

    client = OpenAI(api_key=ZHIPU_API_KEY, base_url=GLM_BASE_URL)

    user_prompt = (
        f"Research this claim for ticker {ticker}:\n"
        f'Topic: "{topic}"\n\n'
        f"Search for the latest news, SEC filings, and expert analysis. "
        f"Determine if this represents real fundamental change or narrative mispricing. "
        f"Focus on engineering constraints, supply chain, and primary source evidence."
    )

    # Z.AI web_search tool (Web Search in Chat mode).
    # NOTE: exact field name is INSUFFICIENT EVIDENCE from official docs
    # (docs.z.ai pages did not render during verification). We use the most
    # commonly documented field and DEGRADE GRACEFULLY if the API rejects it:
    # on any tool-related error we retry WITHOUT tools and mark the result's
    # evidence conservatively. This is more robust than betting on one schema.
    tools = [
        {
            "type": "web_search",
            "web_search": {
                "enable": True,
            }
        }
    ]

    def _call(with_tools: bool):
        kwargs = dict(
            model=GLM_MODEL, max_tokens=1024, temperature=0.0,
            messages=[
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
        )
        if with_tools:
            kwargs["tools"] = tools
        return client.chat.completions.create(**kwargs)

    def _parse(raw):
        if not raw:
            return None
        raw = raw.strip().removeprefix("```json").removeprefix("```")
        raw = raw.removesuffix("```").strip()
        return json.loads(raw)

    runs = []
    searched = True   # track whether web_search was actually available
    for attempt in range(max(1, n_runs)):
        try:
            try:
                response = _call(with_tools=True)
            except Exception as tool_err:
                # Generalized tool degradation: ANY tool/param rejection →
                # retry without tools rather than crash the pipeline.
                if any(w in str(tool_err).lower()
                       for w in ("tool", "function", "param", "web_search", "schema")):
                    print(f"  [RESEARCH] Tool call rejected ({tool_err}). "
                          f"Degrading to no-search mode.")
                    searched = False
                    response = _call(with_tools=False)
                else:
                    raise

            raw = response.choices[0].message.content
            if raw is None:
                # Model used tool but returned no final text — re-ask without tools
                print(f"  [RESEARCH] Attempt {attempt+1}: tool used, no content. Retrying.")
                response = _call(with_tools=False)
                raw = response.choices[0].message.content

            result = _parse(raw)
            if result is None:
                continue
            # If we had to degrade, cap evidence so we never overclaim
            if not searched and result.get("evidence_strength") in ("strong", "moderate"):
                result["evidence_strength"] = "weak"
                result["reasoning"] = ("[NO WEB SEARCH — model used training "
                                       "knowledge only] ") + str(result.get("reasoning", ""))
            runs.append(result)

        except json.JSONDecodeError as e:
            print(f"  [RESEARCH] JSON parse failed attempt {attempt+1}: {e}")
        except Exception as e:
            print(f"  [RESEARCH] API error attempt {attempt+1}: {e}")

    if not runs:
        return None
    return runs[0] if len(runs) == 1 else _aggregate(runs)


def _aggregate(runs: list[dict]) -> dict:
    """Aggregate multiple runs: median for numeric, mode for strings."""
    from collections import Counter
    merged = {}
    numeric = {"feasibility_score", "constraint_penalty"}
    for k in runs[0].keys():
        vals = [r[k] for r in runs if k in r]
        if not vals:
            continue
        if k in numeric:
            floats = sorted(float(v) for v in vals)
            merged[k] = floats[len(floats) // 2]
        elif isinstance(vals[0], list):
            merged[k] = vals[0]
        else:
            c = Counter(str(v) for v in vals)
            best = c.most_common(1)[0][0]
            merged[k] = next((v for v in vals if str(v) == best), vals[0])
    return merged



# ── Compatibility bridge to NRS-1 v3 ──────────────────────────────────────────

def to_reality_object(result: dict):
    """
    Convert research() output to a NRS-1 v3 RealityObject.
    Allows research_agent to plug directly into the existing Gap Index pipeline.

    from research_agent import research, to_reality_object
    from nrs1_v3 import RealityObject, NarrativeObject, compute_gap_index
    """
    try:
        from nrs1_v3 import RealityObject
    except ImportError:
        return result  # Standalone mode: return raw dict

    EVIDENCE_ORDER = ["strong", "moderate", "weak", "insufficient"]
    ev = result.get("evidence_strength", "insufficient")
    if ev not in EVIDENCE_ORDER:
        ev = "insufficient"

    fs = float(result.get("feasibility_score", 0.5))
    cp = float(result.get("constraint_penalty", 0.0))
    cp = min(cp, fs)

    return RealityObject(
        technical_change=result.get("claim", "")[:120],
        feasibility_score=fs,
        constraint_penalty=cp,
        evidence_strength=ev,
        open_constraints=[result.get("primary_constraint", "")]
                          if result.get("primary_constraint", "none") != "none" else [],
        hardware_constraint=result.get("primary_constraint", "not assessed"),
        supply_chain_risk="see verdict_explanation",
        evidence_ceiling="strong",  # Not tier-capped — agent found its own sources
        primary_constraint=result.get("primary_constraint", ""),
        comparable_events=result.get("comparable_events", []),
    )


def to_narrative_object(result: dict, ticker: str):
    """Convert research() output to NRS-1 v3 NarrativeObject."""
    try:
        from nrs1_v3 import NarrativeObject
    except ImportError:
        return result

    direction = result.get("narrative_direction", "neutral")
    sentiment_map = {"bullish": 0.8, "bearish": -0.8, "neutral": 0.0}
    sentiment = sentiment_map.get(direction, 0.0)

    verdict = result.get("verdict", "INSUFFICIENT_EVIDENCE")
    certainty = result.get("confidence", "low")

    return NarrativeObject(
        claim=result.get("claim", ""),
        source_url=", ".join(result.get("key_sources", [])[:2]),
        sentiment_polarity=sentiment,
        propagation=0.75,       # Research agent assumes moderate circulation
        novelty="first_report", # This is a fresh research run
        certainty=certainty if certainty in ("high","moderate","low") else "low",
        source_tier=1,           # Agent found primary sources directly
        source_name="ResearchAgent/GLM",
        doc_type="agent_research",
        verbatim_quote=result.get("evidence_summary", "")[:120],
    )


# ── Standalone interactive mode ────────────────────────────────────────────────

def _print_result(result: dict, ticker: str, topic: str) -> None:
    """Pretty-print research result in the NRS-1 terminal style."""
    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  NRS-1 Research Agent  |  {ticker}  |  {GLM_MODEL}")
    print(sep)
    print(f"  Topic:   {topic}")
    print(f"  Claim:   {result.get('claim','—')[:75]}...")
    print(f"  Verdict: {result.get('verdict','—')}")
    print(f"           {VERDICTS.get(result.get('verdict',''), '')[:70]}")
    print(f"  Confidence: {result.get('confidence','—')}")
    print()
    print(f"  Feasibility:  {result.get('feasibility_score', '—'):.2f}  "
          f"| Constraint: {result.get('constraint_penalty', '—'):.2f}  "
          f"| Evidence: {result.get('evidence_strength','—')}")
    print(f"  Primary constraint: {result.get('primary_constraint','—')}")
    print()
    print(f"  Evidence summary:")
    summary = result.get("evidence_summary", "(none)")
    for chunk in [summary[i:i+65] for i in range(0, len(summary), 65)]:
        print(f"    {chunk}")
    sources = result.get("key_sources", [])
    if sources:
        print(f"\n  Sources ({len(sources)}):")
        for s in sources[:4]:
            print(f"    - {s[:70]}")
    comparables = result.get("comparable_events", [])
    if comparables:
        print(f"\n  Historical analogues:")
        for c in comparables[:3]:
            print(f"    - {c}")
    print()
    print(f"  Verdict explanation:")
    explanation = result.get("verdict_explanation", "(none)")
    for chunk in [explanation[i:i+65] for i in range(0, len(explanation), 65)]:
        print(f"    {chunk}")
    print(sep)
    print("  Not investment advice. Scores experimental.")
    print(sep + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        print("Usage: python research_agent.py <TICKER> <TOPIC>")
        print()
        print("Examples:")
        print('  python research_agent.py NVDA "Blackwell GPU production ramp 2026"')
        print('  python research_agent.py AMD  "MI400 AI accelerator launch news"')
        print('  python research_agent.py TSM  "N2 node yield and ramp progress"')
        print('  python research_agent.py INTC "Intel 18A process node status"')
        sys.exit(0)

    ticker_arg = sys.argv[1].upper()
    topic_arg  = " ".join(sys.argv[2:])

    print(f"\n[RESEARCH] Querying GLM ({GLM_MODEL}) with web search...")
    print(f"  Ticker: {ticker_arg}  Topic: {topic_arg}\n")

    result = research(ticker_arg, topic_arg)

    if result is None:
        print("[RESEARCH] No result returned. Check ZHIPU_API_KEY and model availability.")
        sys.exit(1)

    _print_result(result, ticker_arg, topic_arg)

    # Show NRS-1 compatibility
    print("[COMPAT] Converting to NRS-1 v3 objects...")
    r_obj = to_reality_object(result)
    n_obj = to_narrative_object(result, ticker_arg)
    print(f"  RealityObject: feasibility={r_obj.feasibility_score:.2f} "
          f"evidence={r_obj.evidence_strength}")
    print(f"  NarrativeObject: sentiment={n_obj.sentiment_polarity:.2f} "
          f"certainty={n_obj.certainty}")
    print()
