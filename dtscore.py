#!/usr/bin/env python3
"""dtscore — evaluate deep tech companies and their markets.

Usage:
  dtscore run "Company Name" [--market "thesis"] [--notes "..."]   research + score
  dtscore gather "Company Name" [--market ...] [-o file.yaml]       auto-pull a profile only
  dtscore score <profile.yaml> [--md reports/out.md]               score an existing profile
  dtscore new <slug> [--company "Name"] [--market "..."]            blank template to fill by hand
  dtscore rubric                                                    print the scoring framework

Auto-pull (run / gather) needs ANTHROPIC_API_KEY. score / new / rubric work fully offline.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scoring import load_rubric, score_profile, blank_profile  # noqa: E402
from report import print_report, to_markdown  # noqa: E402

COMPANIES = HERE / "companies"
REPORTS = HERE / "reports"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "company"


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _dump_yaml(profile: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # keep the big raw briefing out of the main file footprint but preserve it
    with open(path, "w") as f:
        yaml.safe_dump(profile, f, sort_keys=False, allow_unicode=True, width=100)


def cmd_score(args) -> int:
    path = Path(args.profile)
    if not path.exists():
        # allow bare slug -> companies/<slug>.yaml
        alt = COMPANIES / f"{_slug(args.profile)}.yaml"
        if alt.exists():
            path = alt
        else:
            print(f"Profile not found: {args.profile}", file=sys.stderr)
            return 1
    profile = _load_yaml(path)
    result = score_profile(profile, load_rubric())
    print_report(result)

    md_path = None
    if args.md:
        md_path = Path(args.md)
    elif args.save_md:
        md_path = REPORTS / f"{path.stem}.md"
    if md_path:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(to_markdown(result))
        print(f"\nMarkdown report -> {md_path}")
    return 0


def _do_gather(args) -> tuple[Path, dict]:
    """Run auto-pull and persist the profile. Raises GatherError on failure."""
    from gather import gather
    profile = gather(args.company, market=args.market or "", notes=args.notes or "")
    out = Path(args.output) if args.output else COMPANIES / f"{_slug(args.company)}.yaml"
    _dump_yaml(profile, out)
    print(f"Profile saved -> {out}")
    return out, profile


def cmd_gather(args) -> int:
    from gather import GatherError
    try:
        _do_gather(args)
    except GatherError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2
    return 0


def cmd_run(args) -> int:
    from gather import GatherError
    try:
        out, profile = _do_gather(args)
    except GatherError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2
    result = score_profile(profile, load_rubric())
    print_report(result)
    md_path = REPORTS / f"{Path(out).stem}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(to_markdown(result))
    print(f"\nProfile -> {out}")
    print(f"Markdown report -> {md_path}")
    print("Tip: edit the profile's scores/overrides, then `dtscore score` it again.")
    return 0


def _resolve_profile_path(ref: str) -> Path | None:
    p = Path(ref)
    if p.exists():
        return p
    alt = COMPANIES / f"{_slug(ref)}.yaml"
    return alt if alt.exists() else None


def cmd_signals(args) -> int:
    from signals import funding_signals, market_signals

    path = _resolve_profile_path(args.profile)
    if not path:
        print(f"Profile not found: {args.profile} (create one with `dtscore new`)", file=sys.stderr)
        return 1
    profile = _load_yaml(path)
    facts = profile.get("facts") or {}

    company = args.query or profile.get("company") or path.stem
    keyword = args.keyword or profile.get("market") or company
    investors = [s.strip() for s in args.investors.split(",")] if args.investors else \
        (facts.get("investors") if isinstance(facts.get("investors"), list) else None)
    tam = args.tam if args.tam is not None else facts.get("tam_usd")
    cagr = args.cagr if args.cagr is not None else facts.get("cagr_pct")
    competitors = args.competitors if args.competitors is not None else facts.get("competitor_count")

    print(f"  Funding: querying SEC EDGAR for '{company}'…")
    f = funding_signals(company, cik=args.cik, investors=investors,
                        capital_needed_usd=args.capital_needed)
    if f.score is None and f.candidates:
        print("  EDGAR match is ambiguous. Re-run with --cik <CIK> for the right filer:")
        for c in f.candidates[:8]:
            flag = " (SPV/fund?)" if c["noise"] else ""
            print(f"    CIK {c['cik']}  {c['name']}  [last {c['last']}, {c['count']} filing(s)]{flag}")
    elif f.score is None:
        print(f"  Funding: {f.evidence}")
    else:
        print(f"  Funding health: {f.score}/10 — {f.evidence}")

    print(f"  Market: '{keyword}'" + ("" if not args.no_trends else " (Trends skipped)") + "…")
    m = market_signals(keyword, tam_usd=tam, cagr_pct=cagr,
                       competitor_count=competitors, use_trends=not args.no_trends)
    if m.score is not None:
        print(f"  Market signal: {m.score}/10 — {m.evidence}")
    else:
        print(f"  Market: {m.evidence}")

    # Assemble the signals block + dimension hints (override > manual > signal).
    hints: dict = {}
    if f.score is not None and (fh := f.dimension_hint()):
        hints["capital_intensity"] = fh
    hints.update(m.dimension_hints())

    sig_block: dict = {}
    if f.score is not None:
        sig_block["funding"] = {"score": f.score, "components": f.components,
                                "evidence": f.evidence, "facts": f.facts, "sources": f.sources}
        if f.facts.get("headline_amount_sold_usd"):
            facts["funding_raised_usd"] = f.facts["headline_amount_sold_usd"]
    if m.score is not None:
        sig_block["market"] = {"score": m.score, "components": m.components,
                               "evidence": m.evidence, "facts": m.facts, "sources": m.sources}
    if hints:
        sig_block["dimension_hints"] = hints

    if sig_block:
        profile["signals"] = sig_block
        profile["facts"] = facts
        _dump_yaml(profile, path)
        print(f"\nSignals written -> {path}")
    else:
        print("\nNo signals could be computed (no EDGAR match and no market data).")

    if args.score:
        result = score_profile(profile, load_rubric())
        print_report(result)
        md_path = REPORTS / f"{path.stem}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(to_markdown(result))
        print(f"\nMarkdown report -> {md_path}")
    return 0


def cmd_new(args) -> int:
    company = args.company or args.slug.replace("-", " ").title()
    profile = blank_profile(company=company, market=args.market or "")
    out = COMPANIES / f"{_slug(args.slug)}.yaml"
    if out.exists() and not args.force:
        print(f"{out} already exists. Use --force to overwrite.", file=sys.stderr)
        return 1
    _dump_yaml(profile, out)
    print(f"Blank template -> {out}")
    print("Fill in scores (0-10) and evidence per dimension, then run:")
    print(f"  ./dtscore score {out}")
    return 0


def cmd_rubric(args) -> int:
    r = load_rubric()
    print(f"\n{r['meta']['name']} (v{r['meta']['version']}) — weights sum to "
          f"{sum(d['weight'] for d in r['dimensions'].values())}\n")
    for key, d in r["dimensions"].items():
        print(f"[{d['weight']:>2}]  {d['title']}  ({key})")
        print(f"      {d['question']}")
        for a in sorted(d["anchors"]):
            print(f"        {a:>2}/10  {d['anchors'][a]}")
        print()
    print("Verdict bands:")
    for b in r["bands"]:
        print(f"  >= {b['min']:>2}  {b['label']:<10} {b['note']}")
    print(f"\nMarket-fit subscore uses: {', '.join(r['market_fit_dimensions'])}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dtscore", description="Evaluate deep tech companies and markets.")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gather", help="auto-pull a company profile (needs ANTHROPIC_API_KEY)")
    g.add_argument("company")
    g.add_argument("--market", help="market/thesis to evaluate against")
    g.add_argument("--notes", help="known facts to feed the analyst")
    g.add_argument("-o", "--output", help="output YAML path")
    g.set_defaults(func=cmd_gather)

    r = sub.add_parser("run", help="auto-pull then score in one shot")
    r.add_argument("company")
    r.add_argument("--market", help="market/thesis to evaluate against")
    r.add_argument("--notes", help="known facts to feed the analyst")
    r.add_argument("-o", "--output", help="output YAML path")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("score", help="score an existing profile YAML (offline)")
    s.add_argument("profile", help="path to profile YAML or a company slug")
    s.add_argument("--md", help="write a markdown report to this path")
    s.add_argument("--save-md", action="store_true", help="auto-save markdown to reports/")
    s.set_defaults(func=cmd_score)

    sg = sub.add_parser("signals", help="pull data-backed funding (SEC EDGAR) + market (Google Trends) signals")
    sg.add_argument("profile", help="path to profile YAML or a company slug")
    sg.add_argument("--query", help="company name for EDGAR (default: profile company)")
    sg.add_argument("--cik", help="EDGAR CIK to disambiguate the filer")
    sg.add_argument("--keyword", help="Google Trends search term (default: profile market)")
    sg.add_argument("--investors", help="comma-separated investor names for the quality match")
    sg.add_argument("--capital-needed", type=float, dest="capital_needed",
                    help="USD needed to commercialize (enables the adequacy component)")
    sg.add_argument("--tam", type=float, help="TAM in USD (default: profile facts)")
    sg.add_argument("--cagr", type=float, help="market CAGR %% (default: profile facts)")
    sg.add_argument("--competitors", type=int, help="count of funded competitors")
    sg.add_argument("--no-trends", action="store_true", help="skip the Google Trends call")
    sg.add_argument("--score", action="store_true", help="re-score and print the report after")
    sg.set_defaults(func=cmd_signals)

    n = sub.add_parser("new", help="create a blank profile template to fill by hand")
    n.add_argument("slug")
    n.add_argument("--company", help="display name")
    n.add_argument("--market", help="market/thesis")
    n.add_argument("--force", action="store_true")
    n.set_defaults(func=cmd_new)

    rb = sub.add_parser("rubric", help="print the scoring framework")
    rb.set_defaults(func=cmd_rubric)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
