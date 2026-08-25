# Honest risks & caveats (§14)

1. **DSM isn't off-the-shelf for all of the US.** Deriving it from raw 3DEP point clouds is real
   engineering (compute + volume). Pick a city with confirmed dense, recent LiDAR; lean on Planetary
   Computer's pre-derived DSM (`3dep-lidar-dsm`) where it exists.
2. **No pure-Python raster solar model.** You take a GRASS (install/scripting overhead) or ArcGIS
   (licensing) dependency for the surface pass. **Decided: GRASS `r.sun`.**
3. **Shading is only as good as the DSM + settings.** DTM-instead-of-DSM, an unbuffered AOI, a
   too-small shadow radius, or passing raw irradiance to PVWatts each silently discards inter-building
   shading (§7/§8). Easiest mistakes to make, hardest to notice.
4. **Roof-plane extraction is the weakest link.** Its errors propagate straight into
   capacity/kWh/CO₂. No large public ground-truth for arbitrary US cities (RoofN3D is NYC-specific),
   so validating a new city is nontrivial. Report uncertainty; don't over-claim precision.
5. **Cloud/free-tier limits are real.** Hosted-layer publishing is often credit- or quota-metered;
   batch publishes and confirm caps before committing a platform as the final target.
6. **India ≠ "just swap the adapter."** The geometry/imagery/shading stages need a functional
   downgrade to the block-model path, not a repoint. Scope it as such.

## Inter-building & mutual shading — the traps (§8)

In dense areas, shadows from *neighbouring* buildings are often the single largest correction to raw
roof potential — bigger than tilt or orientation. The mechanism: model on a **DSM**, and the raster
solar-radiation model does horizon/shadow-casting per cell. Six traps:

1. **Use the DSM, never the DTM** for the radiation pass.
2. **Buffer the study area** — a building on the AOI's south edge is shaded by a taller one just
   outside it. Pad the DSM by a margin sized to the tallest plausible shadow-caster.
3. **Set an adequate shadow search distance** — `r.sun`/`r.horizon` and ArcGIS cap how far they look;
   downtown, a far tall tower still matters.
4. **Don't let PVWatts silently undo it.** PVWatts assumes an *unshaded* horizon. Feed it the
   shading-adjusted per-roof insolation (or a shading-loss factor), not the raw NSRDB location value.
5. **Trees and seasonality are only partial.** A single leaf-on LiDAR/NAIP capture misses deciduous
   winter leaf-off — a known, documentable bias.
6. **Resolution sets the ceiling.** At ~1 m LiDAR DSM you resolve building edges and genuine
   inter-building shadows. A coarse (30 m) DEM cannot — this directly bounds the block-model/India path.

## Reference benchmarks — compare & cite, don't reinvent (§15)

- **Google Project Sunroof** — closest end-to-end precedent (CNN roof scoring → RANSAC planes → TMY
  shading → panel placement). Your "how mine differs" section.
- **NREL/NLR rooftop technical potential** — Gagnon et al. (2016), NREL/TP-6A20-65298: 1,118 GW /
  1,432 TWh-yr, ≈39% of US electricity. Your quantitative sanity check.
- **DeepSolar** — Yu et al., *Joule* (2018) — flagship "CV + solar + city-scale" precedent.
