"""Auto-pull layer: research a company with Claude + web search, then
extract a structured, rubric-aligned profile.

Two phases, on purpose:
  1. research()  — Claude with the web_search server tool gathers facts
                   and writes a cited briefing (messy, free-form).
  2. extract()   — Claude maps that briefing onto the rubric schema via
                   structured outputs (clean, validated, deterministic shape).

The model proposes per-dimension 0-10 scores WITH evidence and confidence;
the scoring engine (scoring.py) owns the weighted rollup. Requires
ANTHROPIC_API_KEY. Everything degrades gracefully if the SDK or key is
absent — the CLI falls back to manual mode.

Model: claude-opus-4-8 with adaptive thinking, per Anthropic guidance.
"""
from __future__ import annotations

import os
from typing import Literal, Optional

from scoring import load_rubric

MODEL = "claude-opus-4-8"
RESEARCH_MAX_TOKENS = 8000
EXTRACT_MAX_TOKENS = 8000


class GatherError(RuntimeError):
    pass


def _require_sdk():
    try:
        import anthropic  # noqa
    except ImportError as e:
        raise GatherError(
            "The 'anthropic' package isn't installed.\n"
            "  Install it:  pip install anthropic\n"
            "  Then set your key:  export ANTHROPIC_API_KEY=sk-ant-..."
        ) from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise GatherError(
            "ANTHROPIC_API_KEY is not set, so auto-pull is unavailable.\n"
            "  Set it:  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  Or build the profile by hand:  ./dtscore new <slug>"
        )
    return anthropic


def _pydantic_models():
    """Build the structured-output schema from the live rubric so the two
    never drift apart."""
    from pydantic import BaseModel, Field, create_model

    rubric = load_rubric()
    dim_keys = list(rubric["dimensions"].keys())

    class DimScore(BaseModel):
        score: int = Field(description="0-10, where 10 is best (see the anchor).")
        confidence: Literal["low", "med", "high"]
        evidence: str = Field(description="1-3 sentences justifying the score, with specifics.")
        source: str = Field(description="Where this came from (URL or publication); '' if inferred.")

    # Scores object: one DimScore per dimension, all required.
    score_fields = {k: (DimScore, ...) for k in dim_keys}
    Scores = create_model("Scores", **score_fields, __base__=BaseModel)

    class Facts(BaseModel):
        tam_usd: Optional[float] = Field(description="Total addressable market in USD, null if unknown.")
        cagr_pct: Optional[float] = Field(description="Market CAGR in percent, null if unknown.")
        funding_raised_usd: Optional[float] = None
        stage: Optional[str] = None
        founded: Optional[int] = None
        trl: Optional[int] = Field(default=None, description="Technology Readiness Level 1-9, null if unknown.")
        patents: Optional[int] = None
        regulatory_pathway: Optional[str] = None
        years_to_revenue: Optional[int] = None

    class Profile(BaseModel):
        company: str
        one_liner: str = Field(description="One sentence on what the company does.")
        sector: str
        facts: Facts
        scores: Scores  # type: ignore

    return Profile, dim_keys


def _rubric_brief(rubric: dict) -> str:
    """Compact, model-readable description of every dimension + anchors."""
    lines = []
    for key, d in rubric["dimensions"].items():
        lines.append(f"- {key} ({d['title']}): {d['question']}")
        anchors = d.get("anchors", {})
        # show the 0 / 5 / 10 anchors to calibrate
        for a in (0, 5, 10):
            if a in anchors:
                lines.append(f"    {a}/10 = {anchors[a]}")
    return "\n".join(lines)


def research(client, company: str, market: str = "", notes: str = "") -> tuple[str, list[str]]:
    """Phase 1: web-search-backed briefing. Returns (text, citations)."""
    rubric = load_rubric()
    sys = (
        "You are a rigorous deep-tech investment analyst. You research companies and the "
        "markets they target, and you are skeptical: you separate real, funded customer pull "
        "from technology push, you weigh capital intensity and regulatory/scientific risk, and "
        "you flag where evidence is thin. Cite sources. Prefer primary and recent data."
    )
    target = f'Company: "{company}".'
    if market:
        target += f' Market/thesis to evaluate it against: "{market}".'
    prompt = (
        f"{target}\n\n"
        "Research this company and its market. Use web search. Produce a concise but specific "
        "briefing covering, with numbers where possible:\n"
        "1. What the company does and its core technology (and how mature it is — TRL if inferable).\n"
        "2. The target market: size (TAM/SAM in $), growth (CAGR), and the 'why now'.\n"
        "3. Customer pull: who buys, the pain, budget, evidence of demand vs. tech push.\n"
        "4. Moat / 10x advantage and IP or data defensibility; key competitors.\n"
        "5. Capital intensity and realistic time-to-revenue.\n"
        "6. Regulatory / scientific risk and the approval/validation pathway.\n"
        "7. Team and funding (stage, amount raised, notable investors).\n"
        "Note clearly wherever the public evidence is missing or weak.\n"
    )
    if notes:
        prompt += f"\nAnalyst notes to incorporate (treat as known facts):\n{notes}\n"

    messages = [{"role": "user", "content": prompt}]
    citations: list[str] = []
    last = None
    for _ in range(8):  # allow several web-search resume rounds
        last = client.messages.create(
            model=MODEL,
            max_tokens=RESEARCH_MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=sys,
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            messages=messages,
        )
        if last.stop_reason == "pause_turn":
            # server hit the tool-iteration limit; resume by re-sending
            messages.append({"role": "assistant", "content": last.content})
            continue
        break

    text_parts = []
    for block in last.content:
        if block.type == "text":
            text_parts.append(block.text)
            # collect web-search citations if present on the block
            for cit in (getattr(block, "citations", None) or []):
                url = getattr(cit, "url", None)
                if url:
                    citations.append(url)
    # de-dup citations, preserve order
    seen, uniq = set(), []
    for u in citations:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return "\n".join(text_parts).strip(), uniq


def extract(client, company: str, market: str, briefing: str) -> dict:
    """Phase 2: map the briefing onto the rubric schema via structured output."""
    Profile, dim_keys = _pydantic_models()
    rubric = load_rubric()
    sys = (
        "You convert a research briefing into a structured deep-tech scorecard. "
        "Score each dimension 0-10 using the anchors provided — higher is always better "
        "(for capital_intensity and regulatory_risk, higher means LOWER risk / cheaper / faster). "
        "Be honest and calibrated: reserve 9-10 for genuinely exceptional evidence, and use low "
        "scores and low confidence where the briefing is thin. Every score needs a specific "
        "evidence sentence. Do not invent precise numbers that aren't supported."
    )
    prompt = (
        f"Company: {company}\nMarket/thesis: {market or '(general)'}\n\n"
        f"Rubric dimensions and anchors:\n{_rubric_brief(rubric)}\n\n"
        f"Research briefing:\n\"\"\"\n{briefing}\n\"\"\"\n\n"
        "Fill out the structured profile: the company fields, the facts (use null where unknown), "
        "and a calibrated score for every dimension with evidence and confidence."
    )
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=EXTRACT_MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=sys,
        messages=[{"role": "user", "content": prompt}],
        output_format=Profile,
    )
    parsed = resp.parsed_output
    return _to_profile_dict(parsed, company, market, dim_keys)


def _to_profile_dict(parsed, company: str, market: str, dim_keys: list[str]) -> dict:
    data = parsed.model_dump()
    scores = {}
    for k in dim_keys:
        s = data["scores"][k]
        scores[k] = {
            "score": s["score"],
            "confidence": s["confidence"],
            "evidence": s["evidence"],
            "source": s["source"],
        }
    return {
        "company": data.get("company") or company,
        "one_liner": data.get("one_liner", ""),
        "sector": data.get("sector", ""),
        "market": market,
        "as_of": _today(),
        "facts": data.get("facts", {}),
        "scores": scores,
        "overrides": {},
    }


def _today() -> str:
    # avoid importing datetime at module import time for testability; read env override
    import datetime
    return datetime.date.today().isoformat()


def gather(company: str, market: str = "", notes: str = "", verbose: bool = True) -> dict:
    """Full auto-pull: research -> extract -> profile dict."""
    anthropic = _require_sdk()
    client = anthropic.Anthropic()
    if verbose:
        print(f"  Researching {company} (web search)…")
    briefing, citations = research(client, company, market, notes)
    if not briefing:
        raise GatherError("Research returned no content. Try again or build the profile manually.")
    if verbose:
        print(f"  Got {len(briefing)} chars of briefing, {len(citations)} citation(s). Extracting scores…")
    profile = extract(client, company, market, briefing)

    # stash the raw briefing + citations for auditability
    profile["_briefing"] = briefing
    if citations:
        # seed any empty source fields with the citation list so they show in the report
        joined = "; ".join(citations[:6])
        for s in profile["scores"].values():
            if not s.get("source"):
                s["source"] = joined
    return profile
