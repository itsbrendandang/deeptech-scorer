"""Deterministic scoring engine.

The LLM (or a human) supplies a 0-10 score + evidence per dimension.
This module owns ALL the math: the weighted rollup, the market-fit
subscore, red flags, and the verdict band. Same rubric in -> same
number out, every time. Nothing here calls a model.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

RUBRIC_PATH = Path(__file__).with_name("rubric.yaml")


def load_rubric(path: Path = RUBRIC_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


@dataclass
class DimResult:
    key: str
    title: str
    weight: float
    raw: float            # 0-10 as supplied
    confidence: str
    evidence: str
    source: str
    overridden: bool = False

    @property
    def weighted(self) -> float:
        # contribution to the 0-100 overall, before normalizing by total weight
        return self.raw * self.weight


@dataclass
class ScoreResult:
    company: str
    market: str
    as_of: str
    one_liner: str
    sector: str
    facts: dict
    dims: list[DimResult]
    overall: float                 # 0-100
    market_fit: float              # 0-100, demand-side subscore
    band_label: str
    band_note: str
    red_flags: list[DimResult] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    scale_max: int = 10

    def dim(self, key: str) -> DimResult | None:
        return next((d for d in self.dims if d.key == key), None)


def _band(overall: float, bands: list[dict]) -> tuple[str, str]:
    for b in sorted(bands, key=lambda x: -x["min"]):
        if overall >= b["min"]:
            return b["label"], b["note"]
    return bands[-1]["label"], bands[-1]["note"]


def _coerce_score(value, scale_max: int) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(float(scale_max), v))


def score_profile(profile: dict, rubric: dict | None = None) -> ScoreResult:
    """Apply the rubric to a company profile dict and return a ScoreResult."""
    rubric = rubric or load_rubric()
    scale_max = int(rubric.get("meta", {}).get("scale_max", 10))
    dim_defs: dict = rubric["dimensions"]
    overrides: dict = profile.get("overrides") or {}
    raw_scores: dict = profile.get("scores") or {}

    dims: list[DimResult] = []
    missing: list[str] = []

    for key, d in dim_defs.items():
        entry = raw_scores.get(key) or {}
        # An override is a bare number that pins the score.
        override_val = _coerce_score(overrides.get(key), scale_max)
        base_val = _coerce_score(entry.get("score"), scale_max)

        value = override_val if override_val is not None else base_val
        if value is None:
            missing.append(key)
            value = 0.0  # missing data scores as zero so gaps hurt, not flatter

        dims.append(
            DimResult(
                key=key,
                title=d.get("title", key),
                weight=float(d.get("weight", 0)),
                raw=value,
                confidence=str(entry.get("confidence", "unknown")),
                evidence=str(entry.get("evidence", "")).strip(),
                source=str(entry.get("source", "")).strip(),
                overridden=override_val is not None,
            )
        )

    total_weight = sum(d.weight for d in dims) or 1.0
    overall = sum(d.weighted for d in dims) / (total_weight * scale_max) * 100.0

    # Market-fit subscore: weighted only over the demand-side dimensions.
    mf_keys = set(rubric.get("market_fit_dimensions", []))
    mf_dims = [d for d in dims if d.key in mf_keys]
    mf_weight = sum(d.weight for d in mf_dims) or 1.0
    market_fit = sum(d.weighted for d in mf_dims) / (mf_weight * scale_max) * 100.0

    band_label, band_note = _band(overall, rubric["bands"])

    flag_at = float(rubric.get("red_flag_at", 3))
    red_flags = [d for d in dims if d.raw <= flag_at and d.key not in missing]

    return ScoreResult(
        company=profile.get("company", "Unknown"),
        market=profile.get("market", ""),
        as_of=str(profile.get("as_of", "")),
        one_liner=profile.get("one_liner", ""),
        sector=profile.get("sector", ""),
        facts=profile.get("facts") or {},
        dims=dims,
        overall=round(overall, 1),
        market_fit=round(market_fit, 1),
        band_label=band_label,
        band_note=band_note,
        red_flags=red_flags,
        missing=missing,
        scale_max=scale_max,
    )


def blank_profile(company: str = "", market: str = "") -> dict:
    """A template profile with every dimension present and empty."""
    rubric = load_rubric()
    scores = {
        key: {"score": None, "confidence": "unknown", "evidence": "", "source": ""}
        for key in rubric["dimensions"]
    }
    return {
        "company": company,
        "one_liner": "",
        "sector": "",
        "market": market,
        "as_of": "",
        "facts": {
            "tam_usd": None,
            "cagr_pct": None,
            "funding_raised_usd": None,
            "stage": None,
            "founded": None,
            "trl": None,
            "patents": None,
            "regulatory_pathway": None,
            "years_to_revenue": None,
        },
        "scores": scores,
        "overrides": {},
    }
