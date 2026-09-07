# ADR-0006: Equity data source & dimension — energy burden (DOE LEAD) + ACS, on 2020 tracts

- **Status:** Accepted
- **Date:** 2026-09-07
- **Phase:** 2 (Part 2-1 — census aggregation + equity overlay)
- **Context tags:** equity, census, data-source, reproducibility, tract-vintage

## Context
The roadmap (Phase 2) calls for an "equity overlay" on the census-tract aggregation but names **no
data source and no definition of (dis)advantage**. `data-sources.md` scopes TIGER/Line tract
*boundaries* only — nothing about the demographic/equity variables the overlay needs. This is a real
gap, not an underspecification.

A landscape scan of tract-level equity data (2026-09-07) found a sharp reproducibility split:

- **Census-native + reliable:** US Census **TIGER/Line** (tract geometry) and **ACS 5-year**
  (income, population, households) — both keyed on **2020 tracts**; **DOE LEAD** (energy burden) —
  also built on ACS→**2020 tracts**. All three re-pull cleanly from live endpoints/files.
- **De-hosted + risky:** the federal composite tools — **CEJST** (Justice40 "disadvantaged" flag)
  and **EPA EJScreen** — were removed from federal websites in early 2025 and survive only on
  third-party mirrors (Harvard Dataverse, Zenodo). **CEJST is still on 2010 tracts**, so its GEOIDs
  do **not** join 1:1 to 2020 geometry without a crosswalk + areal reallocation.

For a solar project, the most on-theme equity dimension is **energy burden** ("who would benefit most
from bill savings"); income is the universally-legible cross-check.

## Decision
1. **Equity dimension** = **energy burden** (headline) + **median household income** (secondary). No
   invented composite index.
2. **Data spine** = **TIGER 2020** tract geometry + **ACS 5-year (2023)** demographics + **DOE LEAD
   2022** energy burden — all keyed on **2020 tracts**, joined on a **zero-padded string `GEOID`**.
3. The de-hosted composites (**CEJST / EJScreen**) are an **optional, pinned, clearly-caveated
   overlay only** — never the core equity variable, and never a load-bearing dependency.

## Consequences
- **Reproducible:** every source re-pulls cleanly; no dependency on a de-hosted federal tool or a
  mirror that may vanish. A pipeline that must re-run for the whole District (Part 2-2) needs this.
- **Transparent / teachable:** energy burden and income are legible variables a reader can reason
  about, rather than a black-box composite with hidden weights (fits the self-learning framing).
- **Shared denominator:** energy burden is per-household, which lines up with the per-household
  potential normalization in ADR-0007 — the two equity axes are measured on the same unit.
- **Setup cost:** the ACS pull needs a **free `CENSUS_API_KEY`** in `.env` (alongside `NLR_API_KEY`);
  the Bureau now requires a key on every request.
- **Documented limitation:** dropping CEJST means no official multi-domain disadvantage flag (climate,
  health, transport). Accepted for reproducibility; the composite can be layered later as a caveated
  extra if a pinned mirror copy is vendored into `data/` with provenance notes.

## Alternatives considered
- **CEJST disadvantaged flag as the core equity variable** — rejected: federally de-hosted
  (mirror-only, no updates), and on **2010 tracts** → a GEOID-vintage join hazard against 2020
  geometry, plus it hides its own weighting.
- **ACS median income alone** — rejected as the *headline*: less solar-relevant than energy burden
  (kept as the secondary variable).
- **A continuous composite equity index** — rejected: arbitrary weights hide assumptions
  (see ADR-0007).
