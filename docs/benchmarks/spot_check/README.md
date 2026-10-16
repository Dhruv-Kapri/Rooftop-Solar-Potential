# Spot-check labels (Part 2-4 step 7b, #22)

`labels_12day_gcr.csv`: hand labels for the 19-roof spot-check sample drawn from the `12day_gcr`
city run (#19) by `scripts/spot_check.py` (#20). Schema is `validation.LABEL_COLUMNS`. The sample,
images and per-plane parquets are in `outputs/dc/12day_gcr/spot_check/` (gitignored, regenerable).
This CSV is versioned here because hand labels can't be regenerated. The tuning ticket (#23) reads it.

This is an **indicative sample, not ground truth** (risks §14.4).

## Provenance

- **Drafted by Claude (2026-10-16) from the per-roof panels. It needs a human review pass before
  #23 relies on it.** Labels came from the DC 2023 orthophoto and DSM hillshade. The model panels
  are in the same image, so the drafter saw them; read the labels with that bias in mind.
- Low-confidence rows say so in `notes` (tree cover, deep shadow, blurry imagery, merged footprints).

## Conventions

- `n_major_planes`: distinct planar roof surfaces covering roughly ≥10% of the roof, as a plane fitter
  should see them. Pitched facets count separately, and so do flat levels at clearly different
  heights (e.g. a raised upper roof). Parapet steps, light wells and small rowhouse height offsets
  don't count. For whole-block footprints that merge several buildings, the count is rough.
- `has_obstruction`: `y` if anything sits on the roof that would block a PV array (HVAC,
  hatches, skylights, dormers, chimneys, penthouses, cupolas, existing PV), otherwise `n`.
  Trees overhanging the roof are noted, not counted.

## Scoring against the stop rule (#23)

- Leave out rows whose `notes` start with `EXCLUDE`: `7_2_594` and `6_4_34` look like a non-roof
  surface and a shadowed alley. Score the other 17.
- Compare `min(n_major_planes, MAX_PLANES)` with the model's `n_planes`. Five rows are labelled above
  the cap: `6_4_28`, `6_4_42`, `6_3_74`, `6_4_55`, `6_4_56`. Without the clamp they can never meet ±1, so they
  would count against tuning when the real problem is the cap or the merged footprint.
- The ±1 tolerance covers the "would make it 2/1" ambiguities in `notes`.

## What the draft shows (input for #23)

- Glover Park flat rowhouse roofs with HVAC (`7_2_103`, `7_2_364`, `7_2_66`, `7_2_882`) come out of
  multiplane as 3–4 planes (binned hip/complex). The labels say 1 plane + obstructions. This is the
  ADR-0013 facet-vs-obstruction case (risks §14 item 7).
- One real gable (`7_2_587`) is fit as 1 plane.
- Three Farragut "complex" footprints (`6_3_74`, `6_4_55`, `6_4_56`) cover whole blocks of several
  buildings. Plane count is capped by `MAX_PLANES` (4), so these rows mostly test the footprint
  over-merge, not the fitter.
