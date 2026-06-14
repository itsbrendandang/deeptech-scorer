"""Deterministic, data-backed signal providers for dtscore.

Each provider pulls from a free public source and returns a composite
0-10 score with a transparent component breakdown, evidence, and sources.
The LLM is not in this path — these numbers are reproducible from inputs.

  funding.py  -> SEC EDGAR Form D (amount, recency, round count) + investor allowlist
  market.py   -> Google Trends demand momentum + size/growth/competition mappers
"""
from .funding import funding_signals, FundingSignal, search_form_d
from .market import market_signals, MarketSignal

__all__ = [
    "funding_signals", "FundingSignal", "search_form_d",
    "market_signals", "MarketSignal",
]
