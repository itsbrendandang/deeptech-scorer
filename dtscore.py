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
