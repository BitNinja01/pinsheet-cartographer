"""Integration tests for the cartographer server plugin."""
import io
import json
import sys
from pathlib import Path

import pytest

# Ensure pinsheet-server source is importable (needed in CI where plugin is
# a checked-out nested repo and PYTHONPATH may not include the parent).
_parent = Path(__file__).resolve().parent.parent.parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from source.database import set_db_path, init_db


@pytest.fixture
def cartographer_app(tmp_path, monkeypatch):
    """Create a Flask app with cartographer plugin discovered and registered."""
    from source import plugin, plugin_loader

    import source.main as main_mod
    main_mod.limiter.enabled = False

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "drafts").mkdir()
    (data_dir / "plugins" / "pinsheet-cartographer").mkdir(parents=True)

    db_path = str(data_dir / "pinsheet.db")
    set_db_path(db_path)
    init_db()

    import source.store as store_mod
    monkeypatch.setattr(store_mod, "_DATA_DIR", data_dir)

    app = main_mod.app
    original_got_first = getattr(app, "_got_first_request", False)

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test-secret-key"
    app.config["DB_PATH"] = Path(db_path)
    app.config["DATA_DIR"] = data_dir
    app._plugin_blocks = {}
    app._plugin_nav = []
    app._plugin_course_actions = []

    plugins_dir = Path(__file__).parent.parent.parent  # plugins/
    monkeypatch.setattr(plugin_loader, "_plugins_dir", lambda: plugins_dir)

    plugin._plugins.clear()
    plugin_loader.discover_plugins(app)

    yield app

    for p in list(plugin._plugins):
        if hasattr(p, "unregister"):
            p.unregister(app)
    plugin._plugins.clear()
    app._plugin_blocks.clear()
    app._plugin_nav.clear()
    app._got_first_request = original_got_first

    # Unregister plugin blueprints from the Flask app
    for bp_name in list(app.blueprints.keys()):
        if bp_name.startswith("cartographer"):
            del app.blueprints[bp_name]
    static_endpoint = "_plugin_cartographer_static"
    if static_endpoint in app.view_functions:
        del app.view_functions[static_endpoint]


def _write_test_geo(data_dir: Path, course_name: str, hole_data: dict) -> None:
    """Write a courses_geo.json file with test geometry in WGS84 lat/lon."""
    path = data_dir / "plugins" / "pinsheet-cartographer" / "courses_geo.json"
    path.write_text(json.dumps({course_name: {"holes": hole_data}}))


def _make_simple_hole(hole_num: int) -> dict:
    """Return simple hole geometry in WGS84 lat/lon coordinates."""
    import math
    lat = 47.606 + (hole_num - 1) * 0.0015
    lon = -122.330
    fairway = [
        [lat, lon],
        [lat, lon + 0.0020],
        [lat + 0.0080, lon + 0.0020],
        [lat + 0.0080, lon],
    ]
    gx, gy = lat + 0.0090, lon + 0.0010
    green = []
    for i in range(8):
        angle = 2 * math.pi * i / 8
        green.append([gx + 0.0003 * math.cos(angle), gy + 0.0003 * math.sin(angle)])
    return {
        "fairway": [fairway],
        "green": [green],
        "bunkers": [],
        "water": [],
        "rough_boundary": [],
        "paths": [],
        "waterways": [],
        "tee_boxes": {"white": (lat, lon)},
    }


class TestCartographerRegistration:
    def test_plugin_info_has_required_fields(self):
        import cartographer
        info = cartographer.plugin_info
        assert info["name"] == "cartographer"
        assert "version" in info
        assert "description" in info

    def test_register_sets_config_defaults(self, cartographer_app):
        assert cartographer_app.config.get("plugins.cartographer.yardage_arcs") is True
        distances = cartographer_app.config.get("plugins.cartographer.yardage_arc_distances")
        assert distances == [100, 125, 150, 175, 200]

    def test_register_adds_nav_item(self, cartographer_app):
        nav = cartographer_app._plugin_nav
        found = any(item["label"] == "Course Maps" for item in nav)
        assert found

    def test_register_creates_db_table(self, cartographer_app):
        import sqlite3
        db = sqlite3.connect(str(cartographer_app.config["DB_PATH"]))
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='plugin_cartographer_geometry'"
        )
        assert cursor.fetchone() is not None
        db.close()

    def test_register_injects_head_block(self, cartographer_app):
        head = cartographer_app._plugin_blocks.get("head", "")
        assert "cartographer.css" in head


class TestCoursePicker:
    def test_empty_no_courses(self, cartographer_app):
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/")
            assert resp.status_code == 200
            assert b"No Courses" in resp.data or b"No courses" in resp.data

    def test_lists_courses_with_geometry(self, cartographer_app):
        _write_test_geo(cartographer_app.config["DATA_DIR"], "Test GC", {
            "1": _make_simple_hole(1),
        })
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/")
            assert resp.status_code == 200
            assert b"Test GC" in resp.data


class TestHoleViewer:
    def test_missing_course_returns_404(self, cartographer_app):
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/NoSuchCourse/hole/1")
            assert resp.status_code == 404
            assert b"No geometry data" in resp.data

    def test_out_of_range_hole_returns_404(self, cartographer_app):
        _write_test_geo(cartographer_app.config["DATA_DIR"], "Test GC", {
            "1": _make_simple_hole(1),
        })
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/Test GC/hole/19")
            assert resp.status_code == 404
            assert b"Hole 19 not found" in resp.data

    def test_valid_hole_returns_svg(self, cartographer_app):
        _write_test_geo(cartographer_app.config["DATA_DIR"], "Test GC", {
            "1": _make_simple_hole(1),
        })
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/Test GC/hole/1")
            assert resp.status_code == 200
            # SVG is in the response (inline SVG, not <img>)
            assert b"<svg" in resp.data


class TestCourseGallery:
    def test_missing_course_returns_404(self, cartographer_app):
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/NoSuchCourse/gallery")
            assert resp.status_code == 404
            assert b"No geometry data" in resp.data

    def test_renders_hole_cards(self, cartographer_app):
        holes = {str(i): _make_simple_hole(i) for i in range(1, 19)}
        _write_test_geo(cartographer_app.config["DATA_DIR"], "Test GC", holes)
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/Test GC/gallery")
            assert resp.status_code == 200
            # 18 <a class="carto-hole-card"> elements
            assert resp.data.count(b"carto-hole-card") >= 18


class TestDataDirResolution:
    def test_server_data_dir_overrides_default(self, tmp_path):
        """When _server_data_dir is set, _get_plugin_data_dir returns it."""
        from cartographer.data import _get_plugin_data_dir

        import cartographer.data as carto_data
        original = carto_data._server_data_dir

        try:
            server_path = tmp_path / "custom_data" / "plugins" / "pinsheet-cartographer"
            carto_data._server_data_dir = server_path
            result = _get_plugin_data_dir()
            assert result == server_path
            assert result.exists()
        finally:
            carto_data._server_data_dir = original


def _write_test_osm(data_dir, course_name):
    """Write a minimal .osm file with one way for testing."""
    import pathlib
    osm_dir = data_dir / "plugins" / "pinsheet-cartographer" / "osm"
    osm_dir.mkdir(parents=True, exist_ok=True)
    osm_path = osm_dir / f"{course_name}.osm"
    osm_path.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <bounds minlat="47.60" minlon="-122.34" maxlat="47.62" maxlon="-122.32"/>
  <way id="1" visible="true">
    <nd ref="10"/>
    <nd ref="11"/>
    <nd ref="12"/>
    <nd ref="10"/>
    <tag k="golf" v="fairway"/>
  </way>
  <node id="10" visible="true" lat="47.606" lon="-122.330"/>
  <node id="11" visible="true" lat="47.607" lon="-122.328"/>
  <node id="12" visible="true" lat="47.606" lon="-122.328"/>
</osm>""")


class TestTaggerRoutes:
    def test_tagger_page_no_osm_returns_200(self, cartographer_app):
        """Tagger page renders even without OSM data (shows error message)."""
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/NoSuchCourse/tag")
            assert resp.status_code == 200
            assert b"No OSM data" in resp.data

    def test_tagger_page_with_osm_includes_script(self, cartographer_app):
        data_dir = cartographer_app.config["DATA_DIR"]
        _write_test_osm(data_dir, "Test GC")
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/Test GC/tag")
            assert resp.status_code == 200
            assert b"API_BASE" in resp.data

    def test_tagger_api_features_no_osm(self, cartographer_app):
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/NoSuchCourse/tag/api/features")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["type"] == "FeatureCollection"
            assert data["features"] == []

    def test_tagger_api_features_with_osm(self, cartographer_app):
        data_dir = cartographer_app.config["DATA_DIR"]
        _write_test_osm(data_dir, "Test GC")
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/Test GC/tag/api/features")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["type"] == "FeatureCollection"
            assert len(data["features"]) > 0

    def test_tagger_api_save_roundtrip(self, cartographer_app):
        data_dir = cartographer_app.config["DATA_DIR"]
        _write_test_osm(data_dir, "Test GC")
        with cartographer_app.test_client() as client:
            save_resp = client.post(
                "/plugins/cartographer/Test GC/tag/api/save",
                json={"scale": {"pixels_per_yard": 1.0}, "holes": {}},
            )
            assert save_resp.status_code == 200
            assert save_resp.get_json()["status"] == "ok"

    def test_tagger_api_splits_no_osm(self, cartographer_app):
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/NoSuchCourse/tag/api/splits")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["type"] == "FeatureCollection"
            assert data["features"] == []

    def test_tagger_api_splits_add_and_list(self, cartographer_app):
        data_dir = cartographer_app.config["DATA_DIR"]
        _write_test_osm(data_dir, "Test GC")
        with cartographer_app.test_client() as client:
            add_resp = client.post(
                "/plugins/cartographer/Test GC/tag/api/splits",
                json=[[47.606, -122.330], [47.607, -122.328]],
            )
            assert add_resp.status_code == 200
            data = add_resp.get_json()
            assert "split_id" in data

            list_resp = client.get("/plugins/cartographer/Test GC/tag/api/splits")
            assert list_resp.status_code == 200
            list_data = list_resp.get_json()
            assert len(list_data["features"]) == 1


class TestOsmUpload:
    def test_upload_valid_osm(self, cartographer_app):
        client = cartographer_app.test_client()
        osm_content = b'<?xml version="1.0"?><osm version="0.6"><node id="1" lat="47.6" lon="-122.3"/></osm>'
        resp = client.post(
            "/plugins/cartographer/Test%20GC/upload-osm",
            data={"osm_file": (io.BytesIO(osm_content), "course.osm")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}

    def test_upload_invalid_extension(self, cartographer_app):
        client = cartographer_app.test_client()
        resp = client.post(
            "/plugins/cartographer/Test%20GC/upload-osm",
            data={"osm_file": (io.BytesIO(b"data"), "course.txt")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "extension" in resp.get_json()["message"]

    def test_upload_invalid_xml(self, cartographer_app):
        client = cartographer_app.test_client()
        resp = client.post(
            "/plugins/cartographer/Test%20GC/upload-osm",
            data={"osm_file": (io.BytesIO(b"not xml at all"), "course.osm")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert "valid" in resp.get_json()["message"]

    def test_upload_no_file(self, cartographer_app):
        client = cartographer_app.test_client()
        resp = client.post(
            "/plugins/cartographer/Test%20GC/upload-osm",
            data={},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_empty_file(self, cartographer_app):
        client = cartographer_app.test_client()
        resp = client.post(
            "/plugins/cartographer/Test%20GC/upload-osm",
            data={"osm_file": (io.BytesIO(b""), "course.osm")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400


class TestPDFExport:
    def test_pdf_config_page_renders(self, cartographer_app):
        holes = {"1": _make_simple_hole(1)}
        _write_test_geo(cartographer_app.config["DATA_DIR"], "Test GC", holes)
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/Test%20GC/pdf")
            assert resp.status_code == 200
            assert b"Generate PDF" in resp.data

    def test_pdf_config_no_geometry(self, cartographer_app):
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/NoSuchCourse/pdf")
            assert resp.status_code == 200
            assert b"no geometry" in resp.data.lower() or b"No geometry" in resp.data

    def test_pdf_generate_no_geo_returns_400(self, cartographer_app):
        with cartographer_app.test_client() as client:
            resp = client.post("/plugins/cartographer/NoSuchCourse/pdf/generate", json={})
            assert resp.status_code == 400
            assert "geometry" in resp.get_json().get("error", "")

    def test_pdf_download_not_found_returns_404(self, cartographer_app):
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/Test%20GC/pdf/download")
            assert resp.status_code == 404

    def test_course_picker_shows_generate_button_without_pdf(self, cartographer_app):
        holes = {"1": _make_simple_hole(1)}
        _write_test_geo(cartographer_app.config["DATA_DIR"], "Test GC", holes)
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/")
            assert resp.status_code == 200
            assert b"Generate PDF" in resp.data

    def test_course_picker_shows_download_with_pdf(self, cartographer_app):
        holes = {"1": _make_simple_hole(1)}
        data_dir = cartographer_app.config["DATA_DIR"]
        _write_test_geo(data_dir, "Test GC", holes)
        import sqlite3
        db = sqlite3.connect(str(cartographer_app.config["DB_PATH"]))
        db.execute(
            "INSERT INTO plugin_cartographer_geometry (user_id, course_name, pdf_generated_at) VALUES (?, ?, ?)",
            (1, "Test GC", "2026-05-15T12:00:00+00:00"),
        )
        db.commit()
        db.close()
        with cartographer_app.test_client() as client:
            resp = client.get("/plugins/cartographer/")
            assert resp.status_code == 200
            assert b"Download" in resp.data
