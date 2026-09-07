# ADR-0008: DOE LEAD energy burden — overall per-tract aggregation from stratum microdata

- **Status:** Accepted
- **Date:** 2026-09-07
- **Phase:** 2 (Part 2-1 — census aggregation + equity overlay)
- **Context tags:** equity, energy-burden, DOE-LEAD, aggregation, data-source

## Context
ADR-0006 fixed **DOE LEAD 2022** as the energy-burden source and ADR-0007 made per-tract energy
burden one of the two equity axes. Neither said **how** to obtain a per-tract burden number, because
the plan (§11) flagged the LEAD file as the least Census-native source and deferred the concrete
mechanics to implementation.

On inspecting the real confirmed file (`data.openei.org/files/6219/DC-2022-LEAD-data.zip`,
`DC AMI Census Tracts 2022.csv`, verified 2026-09-07) the gap became concrete: there is **no
ready-made per-tract energy-burden column**. The file is **row-per-stratum microdata** — one row per
(income-band `AMI150` × tenure/vintage/building/heating-fuel) combination, ~66.5k rows over 204 DC
tracts — with household-count-weighted total columns:

- `HINCP*UNITS` — household income × households (a weighted income total per stratum)
- `ELEP*UNITS`, `GASP*UNITS`, `FULP*UNITS` — electricity / gas / other-fuel cost × households
- `FIP` — the 11-digit tract GEOID, **read back as an int64 by default** (the §11 coercion hazard)

Turning that into one burden per tract requires two decisions: **whose burden** (all households vs a
low-income subset), and **how to aggregate**.

## Decision
1. **Overall tract energy burden**, income-weighted over **all** income bands:
   `energy_burden = Σ(ELEP*UNITS + GASP*UNITS + FULP*UNITS) / Σ(HINCP*UNITS)`, summed over every
   stratum in the tract. Because the `*UNITS` columns are already household-weighted totals, the
   aggregation is a plain per-`FIP` column sum — no re-weighting by the `UNITS` count.
2. Use the **AMI** income-basis file. Summed over all income bands the four bases (AMI/SMI/FPL/LLSI)
   cover the same household universe, so the overall tract total is ~basis-invariant; AMI is the most
   standard.
3. `energy_burden` is a **fraction of income** (units as stored in the source — treated as annual;
   see the assumption below). A tract with total income ≤ 0 yields **NaN**, never 0 (feeds the
   `equity_data_missing` flag, ADR-0007).
4. `FIP` is parsed as a **zero-padded string GEOID** everywhere (never int) — §6/§11.

Implemented as a pure `tracts.aggregate_lead_burden(...)` (groupby-sum, offline unit-tested) fed by
the network `load_energy_burden` loader.

## Consequences
- **Reproducible + simple:** a groupby-sum over confirmed columns; no per-household modelling.
- **Sanity-checked:** on real DC data this yields tract burdens of **0.46%–5.57%** (median ~1.0%),
  which is the expected shape for an *all-households, income-weighted* aggregate — high-income
  households pull the tract-wide ratio down, while the most cost-burdened tract reaches ~5.6%. A
  monthly→annual ×12 on electricity/gas would instead give an impossible ~11%+ aggregate, which is
  the empirical basis for treating the stored `*UNITS` energy columns as already annual.
- **Relative use is robust:** `classify_equity` splits on the **median**, so the absolute level and
  any residual annualization nuance do not change Part 2-1's ranking/smoke conclusion.
- **Known limitation (revisit for Part 2-2's real analysis):** an all-households aggregate is *not*
  the policy-headline "low-income energy burden" (which runs 3–6%+). If the city-wide analysis wants
  the low-income-specific metric, restrict to the lower `AMI150` bands (e.g. ≤80% AMI) — a clean
  extension of the same function. Recorded as an assumption, not silently baked in.

## Alternatives considered
- **Low-income (≤80% AMI) burden** — the policy-standard equity metric, more on-theme, but adds an
  income-threshold choice and suits the city-wide Part 2-2 where the median split is meaningful.
  Deferred, not rejected.
- **Unit-weighted mean of per-stratum burdens** (each household equal) — weights the high-burden
  low-income strata more, closer to the ~3% headline; rejected for Part 2-1 as less simple than the
  aggregate ratio the `*UNITS` columns give directly, but a reasonable Part 2-2 variant.
- **Defer LEAD, use ACS income alone** (ADR-0006 fallback) — rejected here: the aggregation turned
  out cheap once the file was understood, so the on-theme burden axis is worth wiring now.
