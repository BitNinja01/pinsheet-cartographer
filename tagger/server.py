"""Route handler functions for the cartographer visual tagging UI."""
from __future__ import annotations

import logging

from flask import jsonify

log = logging.getLogger("pinsheet")

from ..data import get_osm_path, load_courses_geo_raw, save_courses_geo
from ..osm import parse_osm_file


def _feature_to_shapely(feature: dict):
    """Convert an OSM feature dict to a shapely geometry (lon,lat coords)."""
    from shapely.geometry import Point, Polygon, LineString
    if feature["is_point"]:
        return Point(feature["geometry"][1], feature["geometry"][0])
    coords = [(pt[1], pt[0]) for pt in feature["geometry"]]
    if len(coords) < 3:
        return Point(coords[0])
    if feature["type"] in ("path", "waterway"):
        return LineString(coords)
    return Polygon(coords)


def _shapely_to_geojson_rings(geom) -> list[list[list[float]]]:
    """Convert a shapely Polygon or MultiPolygon to GeoJSON ring coords.

    Returns rings in [lat, lon] order (matching OSM convention).
    """
    from shapely.geometry import Polygon, MultiPolygon
    if isinstance(geom, Polygon):
        return [[[lat, lon] for lon, lat in geom.exterior.coords]]
    if isinstance(geom, MultiPolygon):
        rings = []
        for poly in geom.geoms:
            rings.append([[lat, lon] for lon, lat in poly.exterior.coords])
        return rings
    return []


def _apply_splits(features: list[dict], split_lines: dict) -> list[dict]:
    """Apply split lines to features.

    For each split line, clips any intersecting non-course-wide feature.
    Clipped pieces are stored in `_split_pieces` as GeoJSON-ready
    coordinate lists. Pieces with area < 1% of original are discarded.

    Args:
        features: list of OSM feature dicts with osm_id, type, geometry, is_point, tags.
        split_lines: {split_id: ((lat1, lon1), (lat2, lon2))}

    Returns:
        The same features list, mutated in-place with _split_pieces added
        to intersected features.
    """
    from shapely.geometry import LineString, Polygon, MultiPolygon, Point
    from shapely.ops import split as shapely_split

    course_wide = {"water", "waterway", "path"}

    for split_id, (p1, p2) in split_lines.items():
        split_line = LineString([(p1[1], p1[0]), (p2[1], p2[0])])  # lon,lat

        for feature in features:
            if feature["type"] in course_wide or feature["is_point"]:
                continue

            geom = _feature_to_shapely(feature)
            if isinstance(geom, Point):
                continue

            if not split_line.intersects(geom):
                continue

            pieces = list(shapely_split(geom, split_line).geoms)
            if len(pieces) < 2:
                continue

            total_area = geom.area
            min_area = total_area * 0.01 if total_area > 0 else 0

            feature["_split_pieces"] = []
            for piece in pieces:
                if isinstance(piece, (Polygon, MultiPolygon)) and piece.area >= min_area:
                    feature["_split_pieces"].append(_shapely_to_geojson_rings(piece))

    return features


def _expand_split_features(features: list[dict]) -> list[dict]:
    """Expand split features into sub-features with synthetic IDs.

    Features without _split_pieces pass through unchanged.
    Features with _split_pieces produce one sub-feature per piece,
    with osm_id like 'way/123__0', 'way/123__1' and a 'split_group'
    property linking back to the original osm_id.

    Returns a new flat list (does not mutate input).
    """
    result = []
    for feature in features:
        pieces = feature.get("_split_pieces")
        if not pieces:
            result.append(feature)
            continue

        for i, piece_coords in enumerate(pieces):
            sub = dict(feature)
            sub["osm_id"] = f"{feature['osm_id']}__{i}"
            sub["geometry"] = piece_coords[0] if len(piece_coords) == 1 else piece_coords[0]
            sub["split_group"] = feature["osm_id"]
            sub.pop("_split_pieces", None)
            result.append(sub)

    return result


def _derive_assignments(holes: dict, expanded_features: list[dict]) -> dict:
    """Derive featureAssignments from stored hole data.

    Matches feature IDs in the stored per-hole geometry dicts against
    the expanded feature list (which includes synthetic sub-feature IDs).

    Returns: {osm_id: hole_number} suitable for the frontend.
    """
    assignments = {}
    id_to_feature = {f["osm_id"]: f for f in expanded_features}

    for hole_key, hole_data in holes.items():
        hole_num = int(hole_key)
        for ftype in ("fairway", "green", "bunkers", "water",
                      "waterways", "paths", "rough_boundary"):
            items = hole_data.get(ftype, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and "id" in item:
                        fid = item["id"]
                        if fid in id_to_feature:
                            assignments[fid] = hole_num

    return assignments


def _load_split_config(course_name: str) -> dict:
    """Load split lines from saved geo data in config format.

    Returns: {split_id: ((lat1, lon1), (lat2, lon2))}
    """
    existing = load_courses_geo_raw().get(course_name, {})
    split_lines = existing.get("splits", {})
    return {
        int(sid): ((pts[0][0], pts[0][1]), (pts[1][0], pts[1][1]))
        for sid, pts in split_lines.items()
    }


def handle_get_features(course_name: str):
    """Return parsed OSM features as GeoJSON for Leaflet."""
    osm_path = get_osm_path(course_name)
    if not osm_path.exists():
        return jsonify({"type": "FeatureCollection", "features": [], "course_name": course_name, "bounds": None})

    try:
        features = parse_osm_file(osm_path)
        split_config = _load_split_config(course_name)
        _apply_splits(features, split_config)
    except ImportError as _exc:
        log.warning("cartographer: handle_get_features import error — %s", _exc)
        log.debug("cartographer: import error traceback", exc_info=True)
        return jsonify({"type": "FeatureCollection", "features": [], "course_name": course_name, "bounds": None, "error": str(_exc)})

    golf_types = {"fairway", "green", "bunker", "tee"}
    lats, lons = [], []
    for f in features:
        if f["type"] not in golf_types:
            continue
        if f["is_point"]:
            lats.append(f["geometry"][0])
            lons.append(f["geometry"][1])
        else:
            for pt in f["geometry"]:
                lats.append(pt[0])
                lons.append(pt[1])
    bounds = (
        {"minlat": min(lats), "minlon": min(lons), "maxlat": max(lats), "maxlon": max(lons)}
        if lats else None
    )

    expanded = _expand_split_features(features)
    geojson_features = []
    for f in expanded:
        props = {
            "osm_id": f["osm_id"],
            "type": f["type"],
            "tags": f.get("tags", {}),
        }
        if f.get("split_group"):
            props["split_group"] = f["split_group"]
        if f["is_point"]:
            geojson_features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [f["geometry"][1], f["geometry"][0]]},
                "properties": props,
            })
        else:
            coords = [[pt[1], pt[0]] for pt in f["geometry"]]
            if f["type"] in ("path", "waterway"):
                geojson_features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": props,
                })
            else:
                geojson_features.append({
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [coords]},
                    "properties": props,
                })
    return jsonify({
        "type": "FeatureCollection",
        "features": geojson_features,
        "course_name": course_name,
        "bounds": bounds,
    })


def handle_save(course_name: str, request_data: dict):
    """Save tagged data to courses_geo.json."""
    all_geo = load_courses_geo_raw()
    existing = all_geo.get(course_name, {})
    split_lines = existing.get("splits", {})

    course_data = request_data
    course_data["splits"] = split_lines
    all_geo[course_name] = course_data
    save_courses_geo(all_geo)
    return jsonify({"status": "ok"})


def handle_get_splits(course_name: str):
    """Return split lines as GeoJSON FeatureCollection."""
    split_lines = _load_split_config(course_name)
    split_features = []
    for split_id, (p1, p2) in split_lines.items():
        split_features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[p1[1], p1[0]], [p2[1], p2[0]]],
            },
            "properties": {"split_id": split_id},
        })
    return jsonify({"type": "FeatureCollection", "features": split_features})


def handle_add_split(course_name: str, request_data: list):
    """Add a split line. request_data: [[lat1,lon1],[lat2,lon2]]."""
    p1 = (request_data[0][0], request_data[0][1])
    p2 = (request_data[1][0], request_data[1][1])

    osm_path = get_osm_path(course_name)
    features = parse_osm_file(osm_path)

    all_geo = load_courses_geo_raw()
    existing = all_geo.get(course_name, {})
    split_lines = existing.get("splits", {})
    split_config = {
        int(sid): ((pts[0][0], pts[0][1]), (pts[1][0], pts[1][1]))
        for sid, pts in split_lines.items()
    }

    for f in features:
        f.pop("_split_pieces", None)

    max_id = max(split_config.keys()) if split_config else 0
    new_id = max_id + 1
    split_config[new_id] = (p1, p2)
    _apply_splits(features, split_config)

    affected_ids = [f["osm_id"] for f in features if "_split_pieces" in f]

    split_lines[str(new_id)] = [[p1[0], p1[1]], [p2[0], p2[1]]]
    existing["splits"] = split_lines
    all_geo[course_name] = existing
    save_courses_geo(all_geo)

    return jsonify({"split_id": new_id, "affected": affected_ids})


def handle_delete_split(course_name: str, split_id: int):
    """Remove a split line."""
    all_geo = load_courses_geo_raw()
    existing = all_geo.get(course_name, {})
    split_lines = existing.get("splits", {})

    if str(split_id) not in split_lines:
        return jsonify({"error": "not found"}), 404

    del split_lines[str(split_id)]
    existing["splits"] = split_lines
    all_geo[course_name] = existing
    save_courses_geo(all_geo)

    return jsonify({"status": "ok", "removed": split_id})


def handle_get_assignments(course_name: str):
    """Return existing feature assignments from saved hole data."""
    existing = load_courses_geo_raw().get(course_name, {})
    osm_path = get_osm_path(course_name)
    if not osm_path.exists():
        return jsonify({})
    try:
        features = parse_osm_file(osm_path)
    except ImportError:
        return jsonify({})

    holes = existing.get("holes", {})
    expanded = _expand_split_features(features)
    return jsonify(_derive_assignments(holes, expanded))
