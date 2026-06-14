"""Offline tests for the deterministic parts of the signal providers and
the scoring precedence. No network. Run:  python tests/test_signals.py
(also works under pytest).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signals import funding, market           # noqa: E402
from scoring import score_profile, load_rubric, blank_profile  # noqa: E402


def test_funding_amount_mapping():
    assert funding._amount_score(None) is None
    assert funding._amount_score(2e6) == 3
    assert funding._amount_score(6e7) == 7
    assert funding._amount_score(2e9) == 10


def test_funding_momentum_and_rounds():
    assert funding._momentum_score(6) == 9
    assert funding._momentum_score(30) == 4
    assert funding._momentum_score(48) == 2
    assert funding._rounds_score(1) == 4
    assert funding._rounds_score(5) == 8


def test_investor_allowlist_match():
    assert funding._investor_score(["Sequoia Capital", "Lux Capital"]) == 8.0
    assert funding._investor_score(["Some Angel"]) == 4.0
    assert funding._investor_score(None) is None


def test_market_mappers():
    assert market.size_score(8e9) == 7
    assert market.size_score(5e7) == 0
    assert market.growth_score(22) == 8
    assert market.growth_score(2) == 1
    # competition is U-shaped: validated-but-not-saturated scores highest
    assert market.competition_score(8) == 8
    assert market.competition_score(0) == 4
    assert market.competition_score(80) == 3


def test_market_composite_renormalizes_on_missing_data():
    # only size+growth supplied, trends off -> composite is their weighted blend
    sig = market.market_signals("x", tam_usd=8e9, cagr_pct=22, use_trends=False)
    # size 7 (w .30) + growth 8 (w .20) -> (7*.3 + 8*.2)/.5 = 7.4
    assert sig.score == 7.4
    assert "demand" not in sig.components


def test_scoring_precedence_override_beats_signal():
    p = blank_profile("Test", "test market")
    # signal hint says 8, manual override says 3 -> override wins
    p["signals"] = {"dimension_hints": {"capital_intensity": {"score": 8, "evidence": "sig"}}}
    p["overrides"] = {"capital_intensity": 3}
    r = score_profile(p, load_rubric())
    d = r.dim("capital_intensity")
    assert d.raw == 3 and d.source_kind == "override"


def test_scoring_signal_fills_missing_dimension():
    p = blank_profile("Test", "test market")
    p["signals"] = {"dimension_hints": {"market_size": {"score": 9, "evidence": "tam"}}}
    r = score_profile(p, load_rubric())
    d = r.dim("market_size")
    assert d.raw == 9 and d.source_kind == "signal"
    assert "market_size" not in r.missing


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} tests passed")


if __name__ == "__main__":
    _run_all()
