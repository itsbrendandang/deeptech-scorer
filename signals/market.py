"""Market-attractiveness signal.

Composite of four components, each 0-10:
  size        <- log-scaled TAM (from profile facts / research)
  growth      <- CAGR (from profile facts / research)
  demand      <- Google Trends interest slope (free, live; degrades if blocked)
  competition <- U-shaped on the count of funded competitors

Size/growth/competition take numeric inputs (the LLM/research supplies the
raw numbers); demand is fetched live. Everything maps deterministically.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MarketSignal:
    keyword: str
    score: float | None
    components: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    evidence: str = ""
    sources: list[str] = field(default_factory=list)

    def dimension_hints(self) -> dict:
        """Map components onto rubric dimensions, only where data exists."""
        hints: dict[str, dict] = {}
        c = self.components
        src = "; ".join(self.sources[:3])
        # market_size blends size + growth when both present
        size_parts = [(c[k], w) for k, w in [("size", 0.6), ("growth", 0.4)] if k in c]
        if size_parts:
            tw = sum(w for _, w in size_parts)
            val = sum(v * w for v, w in size_parts) / tw
            hints["market_size"] = {
                "score": int(round(val)), "confidence": "med",
                "evidence": self.evidence, "source": src,
            }
        if "demand" in c:
            hints["market_timing"] = {
                "score": int(round(c["demand"])), "confidence": "low",
                "evidence": f"Google Trends demand momentum. {self.evidence}", "source": src,
            }
        return hints


def size_score(tam_usd: float | None) -> float | None:
    if not tam_usd:
        return None
    for thresh, sc in [(5e10, 10), (1e10, 8), (5e9, 7), (1e9, 5), (5e8, 3)]:
        if tam_usd >= thresh:
            return float(sc)
    return 0.0


def growth_score(cagr_pct: float | None) -> float | None:
    if cagr_pct is None:
        return None
    for thresh, sc in [(35, 10), (25, 9), (18, 8), (12, 6.5), (8, 5), (3, 3)]:
        if cagr_pct >= thresh:
            return float(sc)
    return 1.0


def competition_score(n: int | None) -> float | None:
    if n is None:
        return None
    if n <= 0: return 4.0      # empty field: unproven market, not a clean win
    if n <= 2: return 6.0
    if n <= 15: return 8.0     # validated with room
    if n <= 30: return 6.0
    if n <= 50: return 4.0
    return 3.0                 # saturated: margin compression


def demand_score(keyword: str) -> tuple[float | None, dict, str | None]:
    """Google Trends 12-month interest slope -> 0-10. Returns (score, facts, error)."""
    try:
        from pytrends.request import TrendReq
    except ImportError:
        return None, {}, "pytrends not installed (pip install pytrends)"
    try:
        p = TrendReq(hl="en-US", tz=0)
        p.build_payload([keyword], timeframe="today 12-m")
        df = p.interest_over_time()
        if df is None or df.empty:
            return None, {}, "no Trends data for keyword"
        if "isPartial" in df.columns:
            df = df[~df["isPartial"]]
        series = df[keyword].tolist()
        if len(series) < 8:
            return None, {"trend_points": len(series)}, "too few Trends points"
        first = sum(series[:6]) / 6
        last = sum(series[-6:]) / 6
        ratio = (last + 1) / (first + 1)
        if ratio >= 1.5: sc = 9.0
        elif ratio >= 1.15: sc = 7.5
        elif ratio >= 0.9: sc = 5.5
        elif ratio >= 0.7: sc = 4.0
        else: sc = 2.5
        facts = {"trend_first_avg": round(first, 1), "trend_last_avg": round(last, 1),
                 "trend_ratio": round(ratio, 2), "trend_points": len(series)}
        return sc, facts, None
    except Exception as e:  # pytrends throws many transient errors; never fatal
        return None, {}, f"{type(e).__name__}: {str(e)[:120]}"


def market_signals(
    keyword: str,
    tam_usd: float | None = None,
    cagr_pct: float | None = None,
    competitor_count: int | None = None,
    use_trends: bool = True,
) -> MarketSignal:
    comp: dict[str, float] = {}
    facts: dict = {}
    sources: list[str] = []
    notes: list[str] = []

    s = size_score(tam_usd)
    if s is not None:
        comp["size"] = s
        notes.append(f"TAM ${tam_usd/1e9:.1f}B -> {s:.0f}/10")
    g = growth_score(cagr_pct)
    if g is not None:
        comp["growth"] = g
        notes.append(f"CAGR {cagr_pct:.0f}% -> {g:.0f}/10")
    cs = competition_score(competitor_count)
    if cs is not None:
        comp["competition"] = cs
        notes.append(f"{competitor_count} funded competitors -> {cs:.0f}/10")

    if use_trends and keyword:
        d, dfacts, err = demand_score(keyword)
        facts.update(dfacts)
        if d is not None:
            comp["demand"] = d
            sources.append(f"Google Trends: '{keyword}' (12mo)")
            notes.append(f"search interest {dfacts.get('trend_ratio','?')}x -> {d:.0f}/10")
        elif err:
            notes.append(f"demand: unavailable ({err})")

    weights = {"size": 0.30, "growth": 0.20, "demand": 0.25, "competition": 0.25}
    tw = sum(weights[k] for k in comp) or 1.0
    score = round(sum(comp[k] * weights[k] for k in comp) / tw, 1) if comp else None

    evidence = "Market signals: " + "; ".join(notes) + "." if notes else "No market data supplied."
    return MarketSignal(keyword=keyword, score=score, components=comp, facts=facts,
                        evidence=evidence, sources=sources)
