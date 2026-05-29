# plugins/cartographer/osm.py
"""OSM data access for Cartographer.

Parses .osm XML files into feature dicts and optionally fetches
course data from the OSM API via overpy.
"""
from __future__ import annotations

import re
from pathlib import Path

# Maps OSM tag values to internal feature type names
_GOLF_TAG_MAP = {
    "fairway": "fairway",
    "green": "green",
    "bunker": "bunker",
    "water_hazard": "water",
    "tee": "tee",
    "cartpath": "path",
}

def _classify_tags(tags: dict[str, str]) -> str | None:
    """Return the internal feature type, or None to exclude the feature.

    Whitelist approach: only known golf/water/path tag combos are kept.
    Everything else is silently dropped — no blocklist to maintain.
    """
    # Cart paths can co-tag with highway=path — catch first
    if tags.get("golf") == "cartpath":
        return "path"

    golf = tags.get("golf", "")
    if golf in _GOLF_TAG_MAP:
        return _GOLF_TAG_MAP[golf]
    if golf == "hole":
        return None

    # Water features — check before general exclusion since they may
    # co-tag with bridge=yes, tunnel=culvert for under-path segments.
    if tags.get("waterway") in ("stream", "river", "ditch", "canal", "drain"):
        return "waterway"
    if tags.get("natural") == "water" or tags.get("water"):
        return "water"

    # Bare grass with no golf tag — treat as fairway
    if tags.get("landuse") == "grass" and not golf:
        return "fairway"

    return None


def _nodes_to_ring(node_ids: list[str], node_coords: dict[str, tuple[float, float]]) -> list[list[float]]:
    """Convert a list of node IDs to a polygon ring [[lat, lon], ...]."""
    ring = []
    for nid in node_ids:
        if nid in node_coords:
            lat, lon = node_coords[nid]
            ring.append([lat, lon])
    return ring


def _parse_osm_xml(path: Path):
    """Parse an .osm XML file, returning (root, iter_fn, findall_fn).

    Uses lxml if available; falls back to stdlib xml.etree.ElementTree.
    """
    try:
        from lxml import etree
        tree = etree.parse(str(path))
        root = tree.getroot()
        return root, root.iter, lambda parent, tag: parent.findall(tag)
    except ImportError:
        import xml.etree.ElementTree as ET
        tree = ET.parse(str(path))
        root = tree.getroot()
        def _iter(tag):
            # Strip {*} prefix for stdlib ET (no namespace wildcard support)
            stripped = tag[2:] if tag.startswith("{*}") else tag
            return root.iter(stripped)
        def _findall(parent, tag):
            stripped = tag[2:] if tag.startswith("{*}") else tag
            return parent.findall(stripped)
        return root, _iter, _findall


def parse_osm_file(path: Path) -> list[dict]:
    """Parse a .osm XML file and return a list of feature dicts.

    Each dict has:
      - osm_id: str
      - type: str (fairway/green/bunker/water/rough/tee/unclassified)
      - geometry: list of [lat, lon] pairs (polygon ring) or single [lat, lon] (point)
      - is_point: bool
      - tags: dict of raw OSM tags

    Works with lxml or stdlib xml.etree.ElementTree.
    """
    root, _iter, _findall = _parse_osm_xml(path)

    # Build node coordinate lookup
    node_coords: dict[str, tuple[float, float]] = {}
    for node in _iter("{*}node"):
        nid = node.get("id", "")
        lat = node.get("lat")
        lon = node.get("lon")
        if lat and lon:
            node_coords[nid] = (float(lat), float(lon))

    features = []

    # First pass: collect all way geometry and tags
    way_node_refs: dict[str, list[str]] = {}
    way_tags: dict[str, dict[str, str]] = {}
    for way in _iter("{*}way"):
        osm_id = way.get("id", "")
        node_ids = [nd.get("ref", "") for nd in _findall(way, "nd")]
        tags = {tag.get("k", ""): tag.get("v", "") for tag in _findall(way, "tag")}
        way_node_refs[osm_id] = node_ids
        way_tags[osm_id] = tags

    # Parse multipolygon relations
    used_way_ids: set[str] = set()
    for relation in _iter("{*}relation"):
        tags = {tag.get("k", ""): tag.get("v", "") for tag in _findall(relation, "tag")}
        if tags.get("type") != "multipolygon" and relation.get("type") != "multipolygon":
            continue
        feature_type = _classify_tags(tags)
        if feature_type is None:
            continue
        relation_id = relation.get("id", "")
        outer_idx = 0
        for member in _findall(relation, "member"):
            way_ref = member.get("ref", "")
            role = member.get("role", "")
            if role != "outer":
                continue
            used_way_ids.add(way_ref)
            if way_ref not in way_node_refs:
                continue
            node_ids = way_node_refs[way_ref]
            ring = _nodes_to_ring(node_ids, node_coords)
            if len(ring) >= 3:
                outer_idx += 1
                feature_id = f"{relation_id}_{outer_idx}" if outer_idx > 1 else relation_id
                features.append({
                    "osm_id": feature_id,
                    "type": feature_type,
                    "geometry": ring,
                    "is_point": False,
                    "tags": tags,
                })

    # Parse standalone ways (not consumed by relations)
    for osm_id, tags in way_tags.items():
        if osm_id in used_way_ids:
            continue
        feature_type = _classify_tags(tags)
        if feature_type is None:
            continue
        node_ids = way_node_refs[osm_id]
        ring = _nodes_to_ring(node_ids, node_coords)
        # Closed waterway ways (first node == last node) represent area features
        # (canal basins, river areas) — treat as filled polygons, not linestrings
        if feature_type == "waterway" and len(ring) >= 3 and ring[0] == ring[-1]:
            feature_type = "water"
        min_nodes = 2 if feature_type in ("path", "waterway") else 3
        if len(ring) >= min_nodes:
            features.append({
                "osm_id": osm_id,
                "type": feature_type,
                "geometry": ring,
                "is_point": False,
                "tags": tags,
            })

    # Extract nodes that are golf tees (points)
    for node in _iter("{*}node"):
        tags = {tag.get("k", ""): tag.get("v", "") for tag in _findall(node, "tag")}
        if tags.get("golf") == "tee":
            nid = node.get("id", "")
            lat = node.get("lat")
            lon = node.get("lon")
            if lat and lon:
                features.append({
                    "osm_id": nid,
                    "type": "tee",
                    "geometry": [float(lat), float(lon)],
                    "is_point": True,
                    "tags": tags,
                })

    return features


def fetch_osm_features(course_name: str, save_path: Path) -> list[dict]:
    """Fetch OSM features for a named golf course via the Overpass API.

    Searches for a golf course named `course_name`, fetches all golf-tagged
    features within its bounding box, saves the result as a .osm file at
    `save_path`, and returns the parsed feature list.

    Raises RuntimeError if the course cannot be found or the API call fails.
    """
    import overpy

    api = overpy.Overpass()

    # Step 1: Find the course boundary
    safe_name = re.escape(course_name)
    query = f"""
    [out:xml][timeout:60];
    (
      way["leisure"="golf_course"]["name"~"^{safe_name}$",i];
      relation["leisure"="golf_course"]["name"~"^{safe_name}$",i];
    );
    out body;
    >;
    out skel qt;
    """
    try:
        result = api.query(query)
    except Exception as e:
        raise RuntimeError(f"OSM API fetch failed: {e}") from e

    if not result.ways and not result.relations:
        raise RuntimeError(
            f"Course '{course_name}' not found on OpenStreetMap. "
            "Try downloading the .osm file manually from openstreetmap.org."
        )

    # Step 2: Get bounding box from the first result
    all_lats = [float(n.lat) for w in result.ways for n in w.nodes]
    all_lons = [float(n.lon) for w in result.ways for n in w.nodes]
    if not all_lats:
        raise RuntimeError(f"No node coordinates found for course '{course_name}'.")

    min_lat, max_lat = min(all_lats), max(all_lats)
    min_lon, max_lon = min(all_lons), max(all_lons)
    # Expand bounding box slightly
    pad = 0.001
    bbox = (min_lat - pad, min_lon - pad, max_lat + pad, max_lon + pad)

    # Step 3: Fetch all golf features within the bounding box
    detail_query = f"""
    [out:xml][timeout:120];
    (
      way["golf"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      relation["golf"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      node["golf"="tee"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["natural"="water"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
    );
    out body;
    >;
    out skel qt;
    """
    try:
        detail_result = api.query(detail_query)
    except Exception as e:
        raise RuntimeError(f"OSM feature fetch failed: {e}") from e

    # Step 4: Save raw XML to .osm file
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(detail_result.toxml().encode("utf-8") if hasattr(detail_result, "toxml") else b"")

    # Step 5: Parse and return
    if save_path.stat().st_size == 0:
        # overpy result.toxml() may not be available — re-fetch as raw XML
        import urllib.request
        url = (
            f"https://overpass-api.de/api/interpreter?data="
            f"[out:xml][timeout:120];("
            f'way["golf"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});'
            f'relation["golf"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});'
            f'node["golf"="tee"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});'
            f'way["natural"="water"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});'
            f');out body;>;out skel qt;'
        )
        with urllib.request.urlopen(url, timeout=30) as resp:
            save_path.write_bytes(resp.read())

    return parse_osm_file(save_path)
