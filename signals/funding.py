"""Funding-health signal from SEC EDGAR Form D filings (free, no key).

Pipeline:
  search_form_d(name)        -> candidate filers (disambiguate by CIK)
  fetch_form_d_filings(cik)  -> that filer's D / D/A filings + amounts
  funding_signals(...)       -> composite 0-10 score + breakdown

Form D is the exempt-offering notice every US private raise files. It
gives the amount sold and the date, so it's a clean, public proxy for
funding scale, recency, and round count. Investor quality can't come from
Form D (it lists insiders, not VCs) so it's matched from a provided list
of investor names against a curated allowlist (edit STRATEGIC_INVESTORS).
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

from .http import get, HttpError

FTS_URL = "https://efts.sec.gov/LATEST/search-index?q=%22{q}%22&forms=D,D%2FA"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_XML = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/primary_doc.xml"

# SPV / fund / secondary-vehicle noise to push down in name-based search.
_NOISE = re.compile(r"\b(fund|lp|l\.p\.|spv|series|hiive|forge|trust|holdings? ?ii+|"
                    r"pooled|capital partners|spc|feeder)\b", re.I)

# Curated allowlist of notable / deep-tech-relevant investors. Edit freely —
# matching is case-insensitive substring. This is the one judgment input;
# everything else is computed from filings.
STRATEGIC_INVESTORS = {
    "andreessen", "a16z", "sequoia", "founders fund", "khosla", "lux capital",
    "dcvc", "data collective", "breakthrough energy", "the engine", "playground global",
    "8vc", "bond", "general catalyst", "greylock", "index ventures", "accel",
    "nea", "gv", "google ventures", "in-q-tel", "lowercarbon", "prime movers",
    "bessemer", "obvious ventures", "fifty years", "ses", "y combinator",
    "first round", "felicis", "thrive capital", "coatue", "tiger global",
}


@dataclass
class FormDFiling:
    date: str
    accession: str
    amount_sold: float | None
    offering: str | None
    industry: str | None


@dataclass
class FundingSignal:
    company: str
    cik: str | None
    score: float | None                       # composite 0-10, None if no data
    components: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    evidence: str = ""
    sources: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)  # when CIK ambiguous

    def dimension_hint(self) -> dict | None:
        """Map funding health onto capital_intensity (financing-risk proxy)."""
        if self.score is None:
            return None
        return {
            "score": int(round(self.score)),
            "confidence": "med",
            "evidence": self.evidence,
            "source": "; ".join(self.sources[:4]),
        }


# ----------------------------------------------------------------- discovery

def search_form_d(name: str, limit: int = 15) -> list[dict]:
    """Return candidate Form D filers for a company name (newest first)."""
    data = get(FTS_URL.format(q=_url(name)), as_json=True)
    hits = data.get("hits", {}).get("hits", [])
    by_cik: dict[str, dict] = {}
    for h in hits:
        s = h.get("_source", {})
        names = s.get("display_names", []) or []
        ciks = s.get("ciks") or _ciks_from_names(names)
        for cik in ciks:
            disp = names[0] if names else cik
            ent = by_cik.setdefault(cik, {"cik": cik, "name": disp, "count": 0,
                                          "last": "", "noise": bool(_NOISE.search(disp))})
            ent["count"] += 1
            fd = s.get("file_date", "")
            if fd > ent["last"]:
                ent["last"] = fd
    # rank: non-noise and better name match first, then recency
    target = name.lower()
    def rank(e):
        exactish = target in e["name"].lower()
        return (e["noise"], not exactish, e["last"])
    out = sorted(by_cik.values(), key=rank, reverse=False)
    # recency desc within groups
    out.sort(key=lambda e: e["last"], reverse=True)
    out.sort(key=lambda e: (e["noise"], not (target in e["name"].lower())))
    return out[:limit]


def _ciks_from_names(names: list[str]) -> list[str]:
    out = []
    for n in names:
        m = re.search(r"CIK\s*(\d{10})", n)
        if m:
            out.append(m.group(1))
    return out


# ----------------------------------------------------------------- enumeration

def fetch_form_d_filings(cik: str, max_filings: int = 12) -> list[FormDFiling]:
    cik_int = int(cik)
    data = get(SUBMISSIONS_URL.format(cik=cik_int), as_json=True)
    name = data.get("name", "")
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    out: list[FormDFiling] = []
    for i, form in enumerate(forms):
        if form not in ("D", "D/A"):
            continue
        acc = accs[i]
        amt, offering, industry = _parse_form_d(cik_int, acc)
        out.append(FormDFiling(date=dates[i], accession=acc, amount_sold=amt,
                               offering=offering, industry=industry))
        if len(out) >= max_filings:
            break
    return out, name  # type: ignore


def _parse_form_d(cik_int: int, accession: str) -> tuple[float | None, str | None, str | None]:
    acc = accession.replace("-", "")
    try:
        xml = get(ARCHIVE_XML.format(cik=cik_int, acc=acc))
    except HttpError:
        return None, None, None
    amt = _num(_tag(xml, "totalAmountSold"))
    offering = _tag(xml, "totalOfferingAmount")
    industry = _tag(xml, "industryGroupType")
    return amt, offering, industry


def _tag(xml: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
    return m.group(1).strip() if m else None


def _num(v) -> float | None:
    if not v:
        return None
    try:
        return float(re.sub(r"[^0-9.]", "", v))
    except ValueError:
        return None


# ----------------------------------------------------------------- scoring

def _amount_score(usd: float | None) -> float | None:
    if not usd:
        return None
    for thresh, sc in [(1e9, 10), (5e8, 9), (1e8, 8), (5e7, 7), (1e7, 5.5),
                       (5e6, 4.5), (1e6, 3)]:
        if usd >= thresh:
            return sc
    return 2.0


def _momentum_score(months: float | None) -> float | None:
    if months is None:
        return None
    if months <= 12: return 9.0
    if months <= 18: return 7.0
    if months <= 24: return 6.0
    if months <= 36: return 4.0
    return 2.0


def _rounds_score(n: int) -> float | None:
    if n <= 0: return None
    return {1: 4.0, 2: 6.0, 3: 7.0}.get(n, 8.0)


def _investor_score(investors: list[str] | None) -> float | None:
    if not investors:
        return None
    blob = " ".join(investors).lower()
    matches = sum(1 for k in STRATEGIC_INVESTORS if k in blob)
    if matches >= 3: return 9.5
    if matches == 2: return 8.0
    if matches == 1: return 6.5
    return 4.0


def _adequacy_score(raised: float | None, needed: float | None) -> float | None:
    if not raised or not needed:
        return None
    r = raised / needed
    if r >= 1.0: return 9.0
    if r >= 0.5: return 7.0
    if r >= 0.2: return 5.0
    return 3.0


def _months_since(date_str: str) -> float | None:
    try:
        d = _dt.date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None
    return (_dt.date.today() - d).days / 30.44


def funding_signals(
    company: str,
    cik: str | None = None,
    investors: list[str] | None = None,
    capital_needed_usd: float | None = None,
    today: str | None = None,
) -> FundingSignal:
    """Compute a funding-health signal. If cik is None, resolve by name; if
    the name is ambiguous, return candidates and let the caller pick."""
    sources = ["SEC EDGAR Form D"]

    if cik is None:
        try:
            candidates = search_form_d(company)
        except HttpError as e:
            return FundingSignal(company, None, None, evidence=f"EDGAR search failed: {e}",
                                 sources=sources)
        if not candidates:
            return FundingSignal(company, None, None,
                                 evidence="No Form D filings found on EDGAR (may be non-US or hasn't filed).",
                                 sources=sources)
        # auto-pick only if the top candidate is a clean, unambiguous name match
        top = candidates[0]
        unambiguous = (not top["noise"]) and company.lower() in top["name"].lower()
        if not unambiguous and len(candidates) > 1:
            return FundingSignal(company, None, None, candidates=candidates,
                                 evidence="Ambiguous on EDGAR — pass --cik to pick the right filer.",
                                 sources=sources)
        cik = top["cik"]

    try:
        filings, edgar_name = fetch_form_d_filings(cik)
    except HttpError as e:
        return FundingSignal(company, cik, None, evidence=f"EDGAR fetch failed: {e}", sources=sources)

    if not filings:
        return FundingSignal(company, cik, None,
                             evidence=f"CIK {cik} has no Form D filings.", sources=sources)

    amounts = [f.amount_sold for f in filings if f.amount_sold]
    headline = max(amounts) if amounts else None     # conservative: avoid amendment double-count
    last_date = max(f.date for f in filings)
    months = _months_since(last_date)
    industry = next((f.industry for f in filings if f.industry), None)

    comp: dict[str, float] = {}
    for k, v in [
        ("amount", _amount_score(headline)),
        ("momentum", _momentum_score(months)),
        ("rounds", _rounds_score(len(filings))),
        ("investor_quality", _investor_score(investors)),
        ("capital_adequacy", _adequacy_score(headline, capital_needed_usd)),
    ]:
        if v is not None:
            comp[k] = v

    weights = {"amount": 0.30, "momentum": 0.30, "rounds": 0.15,
               "investor_quality": 0.25, "capital_adequacy": 0.25}
    tw = sum(weights[k] for k in comp) or 1.0
    score = round(sum(comp[k] * weights[k] for k in comp) / tw, 1) if comp else None

    facts = {
        "edgar_name": edgar_name,
        "cik": cik,
        "form_d_count": len(filings),
        "headline_amount_sold_usd": headline,
        "last_filing": last_date,
        "months_since_last": round(months, 1) if months else None,
        "industry_group": industry,
        "filings": [{"date": f.date, "amount_sold": f.amount_sold, "accession": f.accession}
                    for f in filings],
    }
    parts = []
    if headline:
        parts.append(f"largest Form D reports ${headline/1e6:.1f}M sold")
    parts.append(f"{len(filings)} Form D filing(s)")
    if months is not None:
        parts.append(f"last {months:.0f} months ago")
    if "investor_quality" in comp:
        parts.append(f"notable-investor match scores {comp['investor_quality']:.0f}/10")
    if "capital_adequacy" in comp:
        parts.append(f"raised vs. capital needed scores {comp['capital_adequacy']:.0f}/10")
    evidence = f"EDGAR ({edgar_name}, CIK {cik}): " + "; ".join(parts) + "."

    return FundingSignal(
        company=company, cik=cik, score=score, components=comp, facts=facts,
        evidence=evidence,
        sources=sources + [f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=D"],
    )


def _url(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)
