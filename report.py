"""Render a ScoreResult to the terminal and to markdown.

Terminal output uses `rich` if available, otherwise plain ANSI. The
"is this good for the market?" verdict is generated deterministically
from the scores — no model prose — so the narrative can't drift from
the numbers.
"""
from __future__ import annotations

from scoring import ScoreResult, DimResult

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    _RICH = True
except ImportError:  # graceful fallback, no hard dependency
    _RICH = False


def _fmt_money(v) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"${v / div:.1f}{unit}"
    return f"${v:,.0f}"


def _band_color(label: str) -> str:
    return {
        "STRONG": "bright_green",
        "PROMISING": "green",
        "MIXED": "yellow",
        "WEAK": "red",
        "AVOID": "bright_red",
    }.get(label, "white")


def market_verdict(r: ScoreResult) -> str:
    """Deterministic one-paragraph answer to 'is it good for the market?'"""
    mf = r.market_fit
    size = r.dim("market_size")
    timing = r.dim("market_timing")
    pull = r.dim("customer_pull")
    moat = r.dim("moat")

    if mf >= 75:
        lead = "Yes — the market case is strong."
    elif mf >= 60:
        lead = "Probably — the market case is solid but not airtight."
    elif mf >= 45:
        lead = "Mixed — the market is real but the pull or defensibility is shaky."
    else:
        lead = "Not yet — the market case is weak as it stands."

    bits = []
    if size:
        if size.raw >= 7:
            bits.append("the addressable market is large and growing")
        elif size.raw >= 4:
            bits.append("the market is decent but not enormous")
        else:
            bits.append("the market is small or flat")
    if pull:
        if pull.raw >= 7:
            bits.append("there is genuine, funded customer pull")
        elif pull.raw >= 4:
            bits.append("demand exists but inertia/switching cost is a drag")
        else:
            bits.append("this still reads as technology push, not demand")
    if timing and timing.raw >= 7:
        bits.append("the 'why now' is clear")
    elif timing and timing.raw <= 3:
        bits.append("the timing case is unconvincing")
    if moat:
        if moat.raw >= 7:
            bits.append("and the advantage looks defensible")
        elif moat.raw <= 3:
            bits.append("and the moat is thin, so even a good market may not be winnable")

    body = "; ".join(bits)
    return f"{lead} On the evidence, {body}." if body else lead


# ---------------------------------------------------------------- terminal

def print_report(r: ScoreResult) -> None:
    if _RICH:
        _print_rich(r)
    else:
        _print_plain(r)


def _bar(raw: float, scale_max: int, width: int = 10) -> str:
    filled = round(raw / scale_max * width)
    return "█" * filled + "░" * (width - filled)


def _mark(d) -> str:
    if d.source_kind == "override":
        return "*"
    if d.source_kind == "signal":
        return "ƒ"
    return ""


def _signal_summary(sig: dict | None) -> str:
    """One-line component breakdown for a funding/market signal dict."""
    if not sig:
        return ""
    comp = sig.get("components") or {}
    parts = [f"{k} {v:.0f}" for k, v in comp.items()]
    return ", ".join(parts)


def signals_block(r) -> list[str]:
    """Lines describing data-backed signals, or [] if none."""
    lines: list[str] = []
    f, m = r.funding_signal, r.market_signal
    if f and f.get("score") is not None:
        lines.append(f"Funding health   {f['score']:.1f}/10   ({_signal_summary(f)})")
        if f.get("evidence"):
            lines.append(f"  {f['evidence']}")
    if m and m.get("score") is not None:
        lines.append(f"Market signal    {m['score']:.1f}/10   ({_signal_summary(m)})")
        if m.get("evidence"):
            lines.append(f"  {m['evidence']}")
    return lines


def _print_rich(r: ScoreResult) -> None:
    c = Console()
    color = _band_color(r.band_label)

    header = (
        f"[bold]{r.company}[/bold]  ·  {r.sector or 'deep tech'}\n"
        f"[dim]{r.one_liner}[/dim]\n\n"
        f"Overall  [bold {color}]{r.overall:.0f}/100[/bold {color}]  "
        f"[{color}]{r.band_label}[/{color}]    "
        f"Market fit  [bold]{r.market_fit:.0f}/100[/bold]\n"
        f"[dim]{r.band_note}[/dim]"
    )
    sub = f"Market: {r.market}" if r.market else ""
    if r.as_of:
        sub = (sub + f"   ·   as of {r.as_of}").strip()
    if sub:
        header += f"\n[dim]{sub}[/dim]"
    c.print(Panel(header, box=box.ROUNDED, border_style=color))

    t = Table(box=box.SIMPLE_HEAVY, expand=True)
    t.add_column("Dimension", ratio=3)
    t.add_column("Score", justify="center")
    t.add_column("", ratio=2)
    t.add_column("Wt", justify="right")
    t.add_column("Conf", justify="center")
    for d in r.dims:
        sc = f"{d.raw:.0f}{_mark(d)}"
        scolor = "green" if d.raw >= 7 else "yellow" if d.raw >= 4 else "red"
        t.add_row(
            d.title,
            f"[{scolor}]{sc}[/{scolor}]",
            f"[{scolor}]{_bar(d.raw, r.scale_max)}[/{scolor}]",
            f"{d.weight:.0f}",
            d.confidence[:4],
        )
    c.print(t)

    sig_lines = signals_block(r)
    if sig_lines:
        c.print(Panel("\n".join(sig_lines), title="Data signals (EDGAR / Trends)",
                      border_style="blue", box=box.ROUNDED))

    c.print(Panel(market_verdict(r), title="Is it good for the market?",
                  border_style="cyan", box=box.ROUNDED))

    if r.red_flags:
        lines = "\n".join(f"  • [red]{d.title}[/red] ({d.raw:.0f}/10) — {d.evidence or 'no detail'}"
                          for d in r.red_flags)
        c.print(Panel(lines, title="Red flags", border_style="red", box=box.ROUNDED))

    if r.facts:
        f = r.facts
        facts_line = "   ".join(filter(None, [
            f"TAM {_fmt_money(f.get('tam_usd'))}" if f.get('tam_usd') else "",
            f"CAGR {f.get('cagr_pct')}%" if f.get('cagr_pct') is not None else "",
            f"Raised {_fmt_money(f.get('funding_raised_usd'))}" if f.get('funding_raised_usd') else "",
            f"Stage {f.get('stage')}" if f.get('stage') else "",
            f"TRL {f.get('trl')}" if f.get('trl') is not None else "",
            f"~{f.get('years_to_revenue')}y to rev" if f.get('years_to_revenue') is not None else "",
        ]))
        if facts_line.strip():
            c.print(f"[dim]Key facts:[/dim] {facts_line}")

    if r.missing:
        c.print(f"[yellow]Note:[/yellow] no data for {len(r.missing)} dimension(s): "
                f"{', '.join(r.missing)} (scored 0 — fill them in to improve accuracy).")
    legend = []
    if any(d.source_kind == "override" for d in r.dims):
        legend.append("* = manual override")
    if any(d.source_kind == "signal" for d in r.dims):
        legend.append("ƒ = data-backed signal (EDGAR/Trends)")
    if legend:
        c.print(f"[dim]{'   '.join(legend)}[/dim]")


def _print_plain(r: ScoreResult) -> None:
    line = "=" * 64
    print(f"\n{line}\n{r.company}  ·  {r.sector or 'deep tech'}")
    if r.one_liner:
        print(r.one_liner)
    print(line)
    print(f"OVERALL  {r.overall:.0f}/100   {r.band_label}      MARKET FIT  {r.market_fit:.0f}/100")
    print(f"{r.band_note}")
    if r.market:
        print(f"Market: {r.market}" + (f"   as of {r.as_of}" if r.as_of else ""))
    print(line)
    for d in r.dims:
        mk = _mark(d) or " "
        print(f"  {d.raw:>4.0f}/10 {mk} {_bar(d.raw, r.scale_max)}  "
              f"{d.title:<34} (wt {d.weight:.0f}, {d.confidence})")
    print(line)
    sig_lines = signals_block(r)
    if sig_lines:
        print("DATA SIGNALS (EDGAR / Trends):")
        for ln in sig_lines:
            print(("  " + ln) if not ln.startswith("  ") else ln)
        print(line)
    print("IS IT GOOD FOR THE MARKET?")
    print("  " + market_verdict(r))
    if r.red_flags:
        print("\nRED FLAGS:")
        for d in r.red_flags:
            print(f"  ! {d.title} ({d.raw:.0f}/10) — {d.evidence or 'no detail'}")
    if r.missing:
        print(f"\nNote: no data for: {', '.join(r.missing)} (scored 0).")
    print(line + "\n")


# ---------------------------------------------------------------- markdown

def to_markdown(r: ScoreResult) -> str:
    out: list[str] = []
    out.append(f"# {r.company} — Deep Tech Scorecard\n")
    if r.one_liner:
        out.append(f"*{r.one_liner}*\n")
    meta = []
    if r.sector:
        meta.append(f"**Sector:** {r.sector}")
    if r.market:
        meta.append(f"**Market evaluated:** {r.market}")
    if r.as_of:
        meta.append(f"**As of:** {r.as_of}")
    if meta:
        out.append("  \n".join(meta) + "\n")

    out.append(f"## Overall: {r.overall:.0f}/100 — {r.band_label}\n")
    out.append(f"{r.band_note}\n")
    out.append(f"**Market fit (demand-side): {r.market_fit:.0f}/100**\n")
    out.append(f"> **Is it good for the market?** {market_verdict(r)}\n")

    if (r.funding_signal and r.funding_signal.get("score") is not None) or \
       (r.market_signal and r.market_signal.get("score") is not None):
        out.append("## Data signals (EDGAR / Google Trends)\n")
        for label, sig in (("Funding health", r.funding_signal), ("Market signal", r.market_signal)):
            if sig and sig.get("score") is not None:
                comp = ", ".join(f"{k} {v:.0f}" for k, v in (sig.get("components") or {}).items())
                out.append(f"- **{label}: {sig['score']:.1f}/10** ({comp})  ")
                if sig.get("evidence"):
                    out.append(f"  {sig['evidence']}")
        out.append("")

    out.append("## Dimension scores\n")
    out.append("| Dimension | Score | Weight | Confidence | Evidence |")
    out.append("|---|---|---|---|---|")
    for d in r.dims:
        ev = (d.evidence or "").replace("|", "\\|").replace("\n", " ")
        if len(ev) > 220:
            ev = ev[:217] + "..."
        tag = {"override": " *(override)*", "signal": " *(signal)*"}.get(d.source_kind, "")
        out.append(f"| {d.title} | {d.raw:.0f}/10{tag} | {d.weight:.0f} | {d.confidence} | {ev} |")
    out.append("")

    if r.red_flags:
        out.append("## Red flags\n")
        for d in r.red_flags:
            out.append(f"- **{d.title}** ({d.raw:.0f}/10): {d.evidence or 'no detail'}")
        out.append("")

    if r.facts:
        f = r.facts
        out.append("## Key facts\n")
        rows = [
            ("Addressable market (TAM)", _fmt_money(f.get("tam_usd"))),
            ("Market growth (CAGR)", f"{f.get('cagr_pct')}%" if f.get("cagr_pct") is not None else "—"),
            ("Funding raised", _fmt_money(f.get("funding_raised_usd"))),
            ("Stage", f.get("stage") or "—"),
            ("Founded", f.get("founded") or "—"),
            ("Technology readiness (TRL)", f.get("trl") if f.get("trl") is not None else "—"),
            ("Patents", f.get("patents") if f.get("patents") is not None else "—"),
            ("Regulatory pathway", f.get("regulatory_pathway") or "—"),
            ("Years to revenue", f.get("years_to_revenue") if f.get("years_to_revenue") is not None else "—"),
        ]
        out.append("| Field | Value |")
        out.append("|---|---|")
        for k, v in rows:
            out.append(f"| {k} | {v} |")
        out.append("")

    # Sources, de-duplicated, in order of appearance.
    seen: set[str] = set()
    srcs: list[str] = []
    for d in r.dims:
        for s in (d.source or "").split(";"):
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                srcs.append(s)
    if srcs:
        out.append("## Sources\n")
        for s in srcs:
            out.append(f"- {s}")
        out.append("")

    if r.missing:
        out.append(f"> Note: {len(r.missing)} dimension(s) had no data and were scored 0: "
                   f"{', '.join(r.missing)}.\n")

    out.append("---\n")
    out.append("*Scores are weighted per `rubric.yaml`; the overall number is computed "
               "deterministically. Auto-pulled fields should be verified before acting.*")
    return "\n".join(out)
