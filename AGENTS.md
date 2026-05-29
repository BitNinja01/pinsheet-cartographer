# Cartographer — AGENTS.md

Cartographer is a PinSheet plugin that generates golf yardage book PDFs from OpenStreetMap course geometry. It lives at `plugins/cartographer/` as a standalone nested git repo (remote: `BitNinja01/pinsheet-cartographer`).

## Architecture

```
plugin.py          — TUI adapter (PinSheetPlugin): screens, bindings, CSS, settings, fonts
data.py            — Sole persistence gateway: courses_geo.json read/write, OSM/DEM caching
geometry.py        — Haversine, equirectangular projection, hole fitting, Chaikin smoothing
renderer.py        — SVG hole diagrams + green grids (svgwrite), SVG→PNG (cairosvg+PIL)
layout.py          — Page composition: hole pages, stats slots, notes, covers
pdf.py             — PDF pipeline: 20 sheets → 5 saddle-stitch booklets (pypdf)
osm.py             — OSM XML parser, Overpass API fetcher, tag classifier
elevation.py       — DEM download (USGS TNM), elevation shading, contours (rasterio, skimage)
stats.py           — Per-hole stats: fairway/GIR misses, benchmarks, penalties
screens/           — TUI screens: hole_view, course_gallery, geometry_setup, pdf_export
tagger/            — Flask+Leaflet.js browser UI for assigning OSM features to holes
tests/             — pytest: ~174 tests (conftest.py factories, 6 test files)
tools/             — Diagnostic scripts: diag_svg, diag_contours, diag_rotation
fonts/             — JetBrainsMono Nerd Font (installed to ~/.local/share/fonts/ at startup)
docs/              — Session memory: HANDOFF.md, SESSION_LOG.md, DECISIONS.md, RUNBOOK.md
```

### Key relationships
- `data.py` is the **sole persistence gateway** — all reads/writes to `courses_geo.json` go through it
- Dual access pattern: `load_courses_geo()` normalizes to bare rings for consumers; `load_courses_geo_raw()` preserves IDs/splits for the tagger
- `geometry.py` is pure Python + Shapely — no internal cartographer deps
- `renderer.py` depends on `geometry.py` + `data.py`
- `pdf.py` orchestrates `geometry` → `renderer` → `elevation` → `layout` → `data` → `stats`
- Lazy loading: `__init__.py` uses `__getattr__` to defer `CartographerPlugin` import

## Conventions

### Latitude/Longitude ordering
ALL geographic coordinates are `[lat, lon]` (standard Shapely/GeoJSON order). This is the opposite of what many cartography tools use (`[lon, lat]`). Never flip the order.

### Persistence
- **Read**: always through `data.load_courses_geo()` (bare rings) or `data.load_courses_geo_raw()` (with IDs/splits)
- **Write**: always through `data.save_courses_geo()`
- **Don't** read/write `courses_geo.json` directly
- Data directory: `data/plugins/cartographer/` (runtime only; `.gitignore`d)

### Projection
- Equirectangular with course-centroid origin (see `geometry.lat_lon_to_xy`)
- `cos(latitude)` correction on longitude
- Y-axis flipped for north-up SVG output
- `pixels_per_yard` derived from geometry bounding box or set by tagger

### Smoothing
- Chaikin corner-cutting (3 iterations) on all polygon rings
- `chaikin_smooth()` for closed rings; `chaikin_smooth_open()` for open polylines
- `smooth_hole_geometry()` orchestrates opening (morphological cleanup) + smoothing

### Green rendering
- Elevation shading: USGS 1m DEM → rasterio window → PIL grayscale → SVG `<image>` with clipPath
- Contours: `skimage.measure.find_contours` on 2D numpy array
- Break arrows: `np.gradient` on shading PNG → chevron arrowheads along contour polylines
- All green rendering goes through `renderer.render_green()`

### PDF
- 4.25" × 14" individual sheets (306pt × 1008pt), two 7" pages stacked vertically
- Cross-paired: hole N on top, complementary hole (18-N+1) on bottom
- 5 saddle-stitch booklets (8.5" × 14" when assembled)

### Tagger
- Flask server on port 5173, daemon-threaded
- Many-to-many feature-hole assignments (Map-of-Sets)
- Split lines for multi-hole features (Shapely `ops.split()`)
- Style-based visibility: never hide assigned features, use red borders for current-hole

### Tests
- **Framework**: pytest with `unittest.mock`, `monkeypatch`, `tmp_path`
- **Factories** in `conftest.py`: `make_round`, `make_course_geo`, `make_osm_feature`
- No real OSM API calls — all mocked
- Test data uses string-stored numerics matching the real data model

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests (from parent PinSheet repo root)
PYTHONPATH=source:plugins pytest plugins/cartographer/tests/ -v

# Run the tagger standalone
python -m cartographer.tagger "Course Name"

# Generate yardage book standalone
PYTHONPATH=. python -m cartographer.pdf "Course Name" --output /path/to/output

# Compile-check changed Python files
python -m py_compile cartographer/geometry.py
```

## Dependencies

- **Required system**: `libcairo2-dev` (for cairosvg)
- **Python**: shapely, svgwrite, cairosvg, overpy, lxml, flask, pypdf, rasterio, scikit-image, requests
- **Fonts**: JetBrainsMono Nerd Font (shipped in `fonts/`)

## Porting to Server (PinSheet-Server Plugin)

This plugin is currently a **TUI plugin** using PinSheetPlugin (Textual). It needs to be ported to the server's **Blender-style plugin API** (`register(app)`/`unregister(app)` + `plugin_info` dict). See `pinsheet-server/docs/PLUGINS.md` §16.2 for the migration checklist.

Key differences to address:
- `screens()` → Flask Blueprint routes + Jinja2 templates
- `bindings()` → `app._plugin_nav.append(...)` sidebar links
- `css()` → `static/` folder, auto-served at `/plugins/cartographer/static/`
- `on_course_saved(course_name, course_data)` → receive `user_id` and `db_path`
- `settings_schema()` → `app.config` with `plugins.cartographer.` prefix
- Font installation stays in `register()`
- DB writes: create `plugin_cartographer_hole_geometry` table
- All persistence must be per-user scoped (use `user_id` from hooks, never `current_user`)

## Session Memory

This repo uses a session memory framework. Read (in order) on session start:
1. `README.md`
2. `docs/HANDOFF.md`
3. `docs/SESSION_LOG.md` (latest entry)
4. `docs/DECISIONS.md`
5. `docs/RUNBOOK.md`

Update `docs/HANDOFF.md` and append to `docs/SESSION_LOG.md` on session end.
