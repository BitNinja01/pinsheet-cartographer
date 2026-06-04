---
title: Hole 17 elevation shading not rendering (Willows Run - Eagles Talon)
priority: P1
status: open
created: 2026-06-04
---

## Symptom

Elevation shading (heightmap + contours) renders for every green except hole 17.

## Investigation so far

1. The ring normalization fix in `data.py` (`_normalize_hole_features`) was corrected to handle both flat and nested `rings` formats — this fixed a `'float' object is not subscriptable` crash during `_course_green_bounds()`, but hole 17's elevation still doesn't render.
2. `compute_elevation_shading()` returns `None` for hole 17. Possible causes:
   - DEM doesn't cover hole 17's green bbox (DEM was downloaded with old/buggy bounds before the fix)
   - Green elevation range < 0.10m (very flat green — par 3)
   - DEM has NODATA for that area
   - Green polygon issue in `courses_geo.json`

## Diagnostics

Script at `diag_hole17.sh` needs updating to work with current imports. Run with `PYTHONPATH=/opt/pinsheet-server/plugins` to check:
- Green polygon for hole 17 (exists? valid?)
- DEM coverage over hole 17's green bbox
- Elevation range within the green mask
