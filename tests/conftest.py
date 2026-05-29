"""Shared fixtures for Cartographer tests.

Provides synthetic factories that produce data in the same JSON shape
as the real PinSheet data, without depending on OSM API access or
real round data files.
"""
import importlib.util
import sys
from pathlib import Path

import math
import pytest


# -- Module alias: allow 'import cartographer' regardless of plugin directory name
_plugin_dir = Path(__file__).resolve().parent.parent
if _plugin_dir.name != "cartographer":
    _init_path = str(_plugin_dir / "__init__.py")
    _spec = importlib.util.spec_from_file_location("cartographer", _init_path)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["cartographer"] = _mod
    _spec.loader.exec_module(_mod)


# ---------------------------------------------------------------------------
# Round factory — matches PinSheet's JSON shape (string-valued numbers, "H" for hits)
# ---------------------------------------------------------------------------

@pytest.fixture
def make_round():
    """Factory fixture: returns a function that creates a round dict matching
    PinSheet's JSON shape. All numeric fields are strings, hits are "H",
    unrecorded stats are "".

    Defaults produce a bogey-golfer round (85/32/8/6).
    """
    def _make_round(
        course="Test GC",
        date="2026-05-15",
        gross=85,
        putts=32,
        fir_hits=8,
        gir_hits=6,
        penalties=0,
        handicap_index="15.0",
        holes_selection="all",
    ):
        # Standard par layout: 3s on 4/8/12/16, 5s on 2/6/10/14/18
        pars = {
            1: 4, 2: 5, 3: 4, 4: 3, 5: 4, 6: 5, 7: 4, 8: 3, 9: 4,
            10: 5, 11: 4, 12: 3, 13: 4, 14: 5, 15: 4, 16: 3, 17: 4, 18: 5,
        }
        hole_list = list(range(1, 19))
        num_holes = len(hole_list)

        # Distribute gross roughly evenly
        base_gross = gross // num_holes
        extra_gross = gross % num_holes
        gross_per_hole = [base_gross + (1 if i < extra_gross else 0) for i in range(num_holes)]

        # Distribute putts: baseline 2-putts, spread remainder
        base_putts = max(1, putts // num_holes)
        extra_putts = putts - base_putts * num_holes
        putts_per_hole = [base_putts + (1 if i < extra_putts else 0) for i in range(num_holes)]

        # FIR eligible holes (par 4 and 5 only)
        eligible_fir = [n for n in hole_list if pars[n] != 3]
        fir_set = set(eligible_fir[:fir_hits]) if fir_hits <= len(eligible_fir) else set(eligible_fir)

        # GIR
        gir_set = set(hole_list[:gir_hits]) if gir_hits <= num_holes else set(hole_list)

        holes = {}
        for i, n in enumerate(hole_list):
            par = pars[n]
            h_gross = gross_per_hole[i]
            h_putts = putts_per_hole[i] if i < len(putts_per_hole) else 2
            h_fir = "H" if n in fir_set else ("L" if n in eligible_fir else "")
            h_gir = "H" if n in gir_set else ""
            h_pen = "1" if penalties > 0 and i < penalties else "0"

            holes[str(n)] = {
                "gross": str(h_gross),
                "putts": str(h_putts),
                "fairway": h_fir,
                "gir": h_gir,
                "penalties": h_pen,
            }

        return {
            "date": date,
            "course": course,
            "holes_selection": holes_selection,
            "handicap_index": handicap_index,
            "total_gross": str(gross),
            "total_putts": str(putts),
            "holes": holes,
        }
    return _make_round


# ---------------------------------------------------------------------------
# Course geometry factory — projected pixel-coordinate hole geometry
# ---------------------------------------------------------------------------

@pytest.fixture
def make_course_geo():
    """Factory fixture: returns projected hole geometry dicts with
    pixel-coordinate polygon rings for each feature type.

    Default: two holes with simple rectangular fairways and circular greens.
    """
    def _make_course_geo(num_holes=18):
        holes = {}
        for hole_num in range(1, num_holes + 1):
            # Simple rectangular fairway: 100×30 px, offset per hole
            dx = (hole_num - 1) * 20
            fairway = [
                [float(dx), float(40)],
                [float(dx + 100), float(40)],
                [float(dx + 100), float(10)],
                [float(dx), float(10)],
            ]

            # Circular green (approximated as octagon)
            gx, gy = dx + 110, 25.0
            r = 10.0
            green = []
            for i in range(16):
                angle = 2 * math.pi * i / 16
                green.append([gx + r * math.cos(angle), gy + r * math.sin(angle)])

            # One bunker
            bx, by_ = dx + 60, 20.0
            bunker = []
            for i in range(8):
                angle = 2 * math.pi * i / 8
                bunker.append([bx + 8 * math.cos(angle), by_ + 8 * math.sin(angle)])

            holes[str(hole_num)] = {
                "fairway": [fairway],
                "green": [green],
                "bunkers": [bunker],
                "water": [],
                "rough_boundary": [],
                "paths": [],
                "tee_boxes": {"white": (float(dx - 10), float(25))},
            }
        return holes
    return _make_course_geo


# ---------------------------------------------------------------------------
# OSM feature factory — mimics OSM XML parser output
# ---------------------------------------------------------------------------

@pytest.fixture
def make_osm_feature():
    """Factory fixture: creates OSM-like feature dicts matching the output
    of osm.parse_osm_file().
    """
    def _make_osm_feature(
        osm_id="1001",
        feature_type="fairway",
        geometry=None,
        is_point=False,
        tags=None,
    ):
        if geometry is None:
            if is_point:
                geometry = [47.6, -122.3]
            else:
                geometry = [
                    [47.606, -122.330],
                    [47.607, -122.330],
                    [47.607, -122.331],
                    [47.606, -122.331],
                ]
        if tags is None:
            tags = {"golf": feature_type}
        return {
            "osm_id": osm_id,
            "type": feature_type,
            "geometry": geometry,
            "is_point": is_point,
            "tags": tags,
        }
    return _make_osm_feature
