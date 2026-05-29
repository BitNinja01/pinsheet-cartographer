"""Data access layer for pinsheet-cartographer.

All files read/written by cartographer live under data/plugins/cartographer/.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_server_data_dir: Path | None = None


def _get_plugin_data_dir() -> Path:
    """Resolve data/plugins/cartographer/, creating it if needed.

    When running under PinSheet Server, uses the server-configured
    DATA_DIR. Otherwise falls back to parent-repo-relative resolution.
    """
    if _server_data_dir is not None:
        _server_data_dir.mkdir(parents=True, exist_ok=True)
        return _server_data_dir

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent / "data"
    else:
        base = Path(__file__).parent.parent.parent / "data"
    d = base / "plugins" / "cartographer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_plugin_data_dir() -> Path:
    """Return the cartographer data directory, creating it if needed."""
    return _get_plugin_data_dir()


def get_osm_path(course_name: str) -> Path:
    """Return the path to the cached .osm file for a course."""
    osm_dir = _get_plugin_data_dir() / "osm"
    osm_dir.mkdir(exist_ok=True)
    return osm_dir / f"{course_name}.osm"


def _read_raw() -> dict:
    """Read courses_geo.json as-is. Returns empty dict if file doesn't exist."""
    path = _get_plugin_data_dir() / "courses_geo.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_courses_geo_raw() -> dict:
    """Read courses_geo.json with all metadata preserved (IDs, splits).
    
    Used by the tagger server for assignment reconstruction and
    the save/load cycle. Returns the raw JSON dict including feature
    IDs and split lines.
    """
    return _read_raw()


def _normalize_hole_features(hole_data: dict) -> dict:
    """Normalize per-hole feature data to bare ring lists.

    Handles both old format (bare [[[lat,lon],...]] rings) and new
    format ([{"id":"...", "rings":[[...]]}, ...]).
    Only normalizes keys that exist in the input data.
    """
    feature_list_types = {"fairway", "green", "bunkers", "water",
                          "waterways", "paths", "rough_boundary"}
    normalized = {}
    for key, items in hole_data.items():
        if key in feature_list_types:
            if not items:
                normalized[key] = []
            elif isinstance(items[0], dict) and "rings" in items[0]:
                normalized[key] = [item["rings"] for item in items]
            else:
                normalized[key] = items
        else:
            normalized[key] = items
    return normalized


def load_courses_geo() -> dict:
    """Load courses_geo.json, normalized to bare ring lists.

    Old-format data (bare [[[lat,lon]]] rings) passes through unchanged.
    New-format data ([{"id":"...","rings":[[...]]}]) has rings extracted
    and IDs discarded. Returns a dict suitable for geometry/render/PDF.
    """
    raw = _read_raw()
    normalized = {}
    for course_name, course_data in raw.items():
        norm = dict(course_data)
        if "holes" in norm:
            norm["holes"] = {
                hk: _normalize_hole_features(hd)
                for hk, hd in norm["holes"].items()
            }
        normalized[course_name] = norm
    return normalized


def save_courses_geo(data: dict) -> None:
    """Write courses_geo.json."""
    path = _get_plugin_data_dir() / "courses_geo.json"
    path.write_text(json.dumps(data, indent=2))


def get_dem_path(course_name: str) -> Path:
    """Return path to cached course DEM GeoTIFF."""
    dem_dir = _get_plugin_data_dir() / "dem"
    dem_dir.mkdir(exist_ok=True)
    return dem_dir / f"{_course_hash(course_name)}.tif"


def get_contours_cache_path(course_name: str) -> Path:
    """Return path to cached contour data JSON."""
    dem_dir = _get_plugin_data_dir() / "dem"
    dem_dir.mkdir(exist_ok=True)
    return dem_dir / f"{_course_hash(course_name)}_contours.json"


def _course_hash(course_name: str) -> str:
    return hashlib.sha256(course_name.encode()).hexdigest()[:16]
