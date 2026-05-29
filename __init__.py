"""Cartographer plugin for PinSheet Server.

Generates golf yardage book PDFs from OpenStreetMap course geometry.
Provides hole viewer, course gallery, and course picker web pages.
"""
from __future__ import annotations

import importlib
import logging
import shutil
import sqlite3
import subprocess
from pathlib import Path

from flask import Blueprint

log = logging.getLogger("pinsheet")

plugin_info = {
    "name": "cartographer",
    "version": "1.4.0",
    "description": "Course geometry, hole diagrams, and yardage book generation",
    "author": "PinSheet",
}


def _install_fonts() -> None:
    fonts_dir = Path(__file__).parent / "fonts" / "JetBrainsMono"
    target_dir = Path.home() / ".local" / "share" / "fonts" / "pinsheet"
    target_dir.mkdir(parents=True, exist_ok=True)
    needs_cache = False
    for ttf in fonts_dir.glob("*.ttf"):
        dst = target_dir / ttf.name
        if not dst.exists() or dst.stat().st_size != ttf.stat().st_size:
            shutil.copy2(ttf, dst)
            needs_cache = True
    if needs_cache and shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f"], check=False)


def _create_tables(db_path: Path) -> None:
    db = sqlite3.connect(str(db_path))
    db.execute("""
        CREATE TABLE IF NOT EXISTS plugin_cartographer_geometry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_name TEXT NOT NULL,
            tagged_at TEXT,
            pixels_per_yard REAL,
            feature_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, course_name)
        )
    """)
    db.commit()
    db.close()


def register(app):
    # 1. Set server-aware data directory
    carto_data = importlib.import_module(__name__ + ".data")
    carto_data._server_data_dir = Path(app.config["DATA_DIR"]) / "plugins" / "cartographer"
    carto_data._server_data_dir.mkdir(parents=True, exist_ok=True)

    # 2. Install fonts (best-effort)
    try:
        _install_fonts()
    except Exception:
        log.warning("cartographer: font installation failed", exc_info=True)

    # 3. Create DB tables
    try:
        _create_tables(app.config["DB_PATH"])
    except Exception:
        log.warning("cartographer: DB table creation failed", exc_info=True)

    # 4. Log XML parser availability for diagnostic
    try:
        import lxml.etree  # noqa: F401
        log.info("cartographer: using lxml XML parser")
    except ImportError:
        log.info("cartographer: lxml not available — using stdlib xml.etree.ElementTree (slower, fewer features)")

    # 5. Register Blueprint
    try:
        _bp_spec = importlib.util.spec_from_file_location(
            __name__ + ".blueprint", str(Path(__file__).parent / "blueprint.py"),
        )
        _bp_mod = importlib.util.module_from_spec(_bp_spec)
        import sys as _sys
        _sys.modules[_bp_mod.__name__] = _bp_mod
        _bp_spec.loader.exec_module(_bp_mod)
        app.register_blueprint(_bp_mod.bp)

        _csrf = app.extensions.get("csrf")
        if _csrf is not None:
            for _rule in app.url_map.iter_rules():
                if _rule.endpoint and _rule.endpoint.startswith("cartographer.") and "POST" in _rule.methods:
                    _csrf.exempt(app.view_functions[_rule.endpoint])
    except Exception as _exc:
        log.warning("cartographer: blueprint registration failed — %s", _exc)

    # 6. Inject CSS
    head_tag = '<link rel="stylesheet" href="/plugins/cartographer/static/cartographer.css">'
    app._plugin_blocks["head"] = (
        (app._plugin_blocks.get("head", "") + "\n" + head_tag).strip()
    )

    # 7. Add nav link
    app._plugin_nav.append({
        "label": "Course Maps",
        "url": "/plugins/cartographer",
        "page_id": "cartographer",
    })

    # 8. Default settings
    app.config.setdefault("plugins.cartographer.yardage_arcs", True)
    app.config.setdefault("plugins.cartographer.yardage_arc_distances", [100, 125, 150, 175, 200])

    log.info("cartographer: registered v%s", plugin_info["version"])


def unregister(app):
    carto_data = importlib.import_module(__name__ + ".data")
    carto_data._server_data_dir = None
    app.config.pop("plugins.cartographer.yardage_arcs", None)
    app.config.pop("plugins.cartographer.yardage_arc_distances", None)

    head_tag = '<link rel="stylesheet" href="/plugins/cartographer/static/cartographer.css">'
    current_head = app._plugin_blocks.get("head", "")
    app._plugin_blocks["head"] = current_head.replace(head_tag, "").strip()

    app._plugin_nav[:] = [
        entry for entry in app._plugin_nav
        if entry.get("page_id") != "cartographer"
    ]
