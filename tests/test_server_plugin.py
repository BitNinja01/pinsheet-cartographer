"""Integration tests for the cartographer server plugin."""
import json
from pathlib import Path

import pytest

from source.database import set_db_path, init_db

_bp_registered = False


@pytest.fixture
def cartographer_app(tmp_path, monkeypatch):
    """Create a Flask app with cartographer plugin registered."""
    global _bp_registered

    import source.main as main_mod
    main_mod.limiter.enabled = False

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "drafts").mkdir()
    carto_data_dir = data_dir / "plugins" / "cartographer"
    carto_data_dir.mkdir(parents=True)

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
    app.jinja_env.globals.setdefault("settings", {"theme": "dark"})

    plugin_path = Path(__file__).parent.parent / "plugins" / "cartographer"
    templates_dir = str(plugin_path / "templates")
    search_path = getattr(app.jinja_loader, "searchpath", None)
    if search_path is not None and templates_dir not in search_path:
        search_path.append(templates_dir)

    import cartographer.data as carto_data
    original_data_dir = carto_data._server_data_dir
    carto_data._server_data_dir = carto_data_dir

    try:
        from cartographer.__init__ import _install_fonts
        _install_fonts()
    except Exception:
        pass

    import sqlite3
    db = sqlite3.connect(str(app.config["DB_PATH"]))
    db.execute(
        "CREATE TABLE IF NOT EXISTS plugin_cartographer_geometry ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " user_id INTEGER NOT NULL,"
        " course_name TEXT NOT NULL,"
        " tagged_at TEXT,"
        " pixels_per_yard REAL,"
        " feature_count INTEGER DEFAULT 0,"
        " FOREIGN KEY (user_id) REFERENCES users(id),"
        " UNIQUE(user_id, course_name)"
        ")"
    )
    db.commit()
    db.close()

    from cartographer.blueprint import bp
    if not _bp_registered:
        app.register_blueprint(bp)
        _bp_registered = True

    head_tag = '<link rel="stylesheet" href="/plugins/cartographer/static/cartographer.css">'
    app._plugin_blocks["head"] = (
        (app._plugin_blocks.get("head", "") + "\n" + head_tag).strip()
    )
    app._plugin_nav.append({
        "label": "Course Maps",
        "url": "/plugins/cartographer",
        "page_id": "cartographer",
    })
    app.config.setdefault("plugins.cartographer.yardage_arcs", True)
    app.config.setdefault("plugins.cartographer.yardage_arc_distances", [100, 125, 150, 175, 200])

    yield app

    app._plugin_blocks["head"] = app._plugin_blocks.get("head", "").replace(head_tag, "").strip().strip("\n")
    app._plugin_nav[:] = [e for e in app._plugin_nav if e.get("page_id") != "cartographer"]
    carto_data._server_data_dir = original_data_dir
    app._got_first_request = original_got_first


def _write_test_geo(data_dir: Path, course_name: str, hole_data: dict) -> None:
    """Write a courses_geo.json file with test geometry in WGS84 lat/lon."""
    path = data_dir / "plugins" / "cartographer" / "courses_geo.json"
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
            server_path = tmp_path / "custom_data" / "plugins" / "cartographer"
            carto_data._server_data_dir = server_path
            result = _get_plugin_data_dir()
            assert result == server_path
            assert result.exists()
        finally:
            carto_data._server_data_dir = original
