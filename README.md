# dtscore — deep tech company & market evaluator

A CLI that scores any deep tech company against a transparent, deep-tech-specific
rubric and answers one question directly: **is this even good for the market?**

It is **hybrid**: Claude (with web search) auto-pulls the messy facts — market size,
funding, TRL, competitors, regulatory path — and proposes a score per dimension with
evidence. Deterministic code (not the model) does the weighted rollup, so the final
number is consistent, auditable, and tunable. You can override anything by hand.

## How it works

```
  ┌─ gather (Claude + web search) ─┐   ┌─ score (pure Python) ─┐
  │ research the company & market  │   │ weighted rollup       │
  │ extract facts + 0-10 scores    │ → │ market-fit subscore   │ → report (terminal + .md)
  │ with evidence & citations      │   │ red flags + verdict   │
  └────────────────────────────────┘   └───────────────────────┘
        needs ANTHROPIC_API_KEY              fully offline
```

The rubric (`rubric.yaml`) is the brain: 9 dimensions, fixed weights summing to 100,
and explicit 0/3/5/8/10 anchors. Edit it and every future score reflects the change.
Every dimension is scored so **higher is always better** (a 10 on capital-intensity
means cheap/fast; a 10 on regulatory-risk means a clean path).

| Pillar | Dimensions |
|---|---|
| Market (40) | market size & growth, why-now timing, customer pull |
| Moat (32) | 10x advantage, IP/data, technology readiness (TRL) |
| Execution (28) | capital efficiency, regulatory/scientific risk, team fit |

The **market-fit subscore** (the "good for the market?" answer) is a weighted blend of
market size, timing, customer pull, and moat.

## Usage

```bash
cd ~/deeptech-scorer

# One-shot: research + score (needs a key)
export ANTHROPIC_API_KEY=sk-ant-...
./dtscore run "Commonwealth Fusion" --market "grid-scale fusion power"

# Just pull a profile (saved to companies/<slug>.yaml), edit, then score
./dtscore gather "HelixFerm" --market "animal-free dairy" --notes "Series B, 2 LOIs"
./dtscore score companies/helixferm.yaml --save-md

# Fully manual — no key needed
./dtscore new mycompany --company "My Co" --market "..."   # blank template to fill in
./dtscore score companies/mycompany.yaml

# Inspect or tune the framework
./dtscore rubric
```

Commands `score`, `new`, and `rubric` work with **no API key and no network**.
Only `gather` / `run` call Claude.

### Overrides

Trust your own judgment over the model on any dimension — pin it in the profile:

```yaml
overrides:
  capital_intensity: 3   # forces this dimension to 3, marked * in the report
```

Missing dimensions score 0 (gaps hurt the score rather than silently flattering it)
and are listed at the bottom of the report.

## Files

- `rubric.yaml` — dimensions, weights, anchors, verdict bands (edit this to tune the model)
- `gather.py` — Claude + web search → structured profile (model `claude-opus-4-8`, adaptive thinking)
- `scoring.py` — deterministic weighted scoring engine (no model calls)
- `report.py` — terminal + markdown rendering, deterministic market verdict
- `dtscore.py` / `dtscore` — CLI and wrapper
- `companies/` — saved profiles (YAML, hand-editable) · `reports/` — generated markdown
- `companies/helixferm.yaml` — a worked example you can score right now

## Notes & honesty

- The model proposes per-dimension scores; **the rollup math is fixed code**, so the
  overall number can't drift from the weights. But the inputs are still model judgments —
  treat auto-pulled facts as leads to verify, not ground truth.
- Designed to evaluate *any* deep tech company, including your own. Run Bioqore through it.
- Setup: `python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt` (already done here).
