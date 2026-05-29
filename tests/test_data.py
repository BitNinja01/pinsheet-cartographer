"""Tests for cartographer/data.py — JSON persistence layer."""
import json
import sys
from pathlib import Path

from cartographer.data import (
    _get_plugin_data_dir, get_plugin_data_dir, get_osm_path,
    load_courses_geo, load_courses_geo_raw, save_courses_geo,
)


class TestGetPluginDataDir:
    """_get_plugin_data_dir — resolves and creates data directory."""

    def test_returns_path_that_exists(self):
        result = _get_plugin_data_dir()
        assert isinstance(result, Path)
        assert result.is_dir()

    def test_path_structure(self):
        result = _get_plugin_data_dir()
        assert result.name == "pinsheet-cartographer"
        assert result.parent.name == "plugins"
        assert result.parent.parent.name == "data"

    def test_source_mode(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        result = _get_plugin_data_dir()
        assert isinstance(result, Path)
        assert result.is_dir()

    def test_creates_directory_if_missing(self, tmp_path, monkeypatch):
        target = tmp_path / "data" / "plugins" / "pinsheet-cartographer"
        assert not target.exists()
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: target)
        target.mkdir(parents=True, exist_ok=True)
        assert target.is_dir()


class TestGetPluginDataDirPublic:
    """get_plugin_data_dir — public wrapper."""

    def test_returns_path_that_exists(self):
        result = get_plugin_data_dir()
        assert isinstance(result, Path)
        assert result.is_dir()

    def test_same_as_private(self):
        assert get_plugin_data_dir() == _get_plugin_data_dir()


class TestGetOsmPath:
    """get_osm_path — returns path for cached .osm file."""

    def test_normal(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        result = get_osm_path("Test Course")
        assert result == tmp_path / "osm" / "Test Course.osm"

    def test_creates_osm_directory(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        osm_dir = tmp_path / "osm"
        assert not osm_dir.exists()
        get_osm_path("Any Course")
        assert osm_dir.is_dir()

    def test_special_characters(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        name = "St. Andrews (Old Course)"
        result = get_osm_path(name)
        assert result == tmp_path / "osm" / f"{name}.osm"


class TestLoadCoursesGeo:
    """load_courses_geo — loads courses_geo.json."""

    def test_file_exists(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        test_data = {"Test Course": {"holes": {}}}
        (tmp_path / "courses_geo.json").write_text(json.dumps(test_data))
        assert load_courses_geo() == test_data

    def test_file_not_exists(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        assert load_courses_geo() == {}

    def test_empty_json_object(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        (tmp_path / "courses_geo.json").write_text("{}")
        assert load_courses_geo() == {}

    def test_multiple_courses(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        test_data = {
            "Course A": {"holes": {"1": {}}},
            "Course B": {"holes": {"1": {}, "2": {}}},
        }
        (tmp_path / "courses_geo.json").write_text(json.dumps(test_data))
        assert load_courses_geo() == test_data


class TestSaveCoursesGeo:
    """save_courses_geo — writes courses_geo.json."""

    def test_writes_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        data = {"Test Course": {"holes": {}}}
        save_courses_geo(data)
        path = tmp_path / "courses_geo.json"
        assert path.exists()
        assert json.loads(path.read_text()) == data

    def test_readable_by_load(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        data = {"My Course": {"holes": {"1": {"par": 4}}}}
        save_courses_geo(data)
        assert load_courses_geo() == data

    def test_overwrite(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        save_courses_geo({"first": "data"})
        save_courses_geo({"second": "data"})
        path = tmp_path / "courses_geo.json"
        assert json.loads(path.read_text()) == {"second": "data"}

    def test_empty_dict(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        save_courses_geo({})
        path = tmp_path / "courses_geo.json"
        assert path.exists()
        assert json.loads(path.read_text()) == {}

    def test_nested_data(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        data = {
            "Course": {
                "holes": {
                    "1": {"fairway": [[0, 0], [100, 0], [100, 50], [0, 50]]},
                    "2": {"green": [[10, 10], [20, 10], [20, 20], [10, 20]]},
                }
            }
        }
        save_courses_geo(data)
        assert json.loads((tmp_path / "courses_geo.json").read_text()) == data


class TestLoadCoursesGeoNormalized:
    """load_courses_geo — returns bare ring lists."""

    def test_old_format_passthrough(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        data = {
            "Course": {
                "holes": {
                    "1": {"green": [[[0, 0], [10, 0], [10, 10]]]},
                    "2": {"fairway": [[[0, 0], [20, 0], [20, 20]]]},
                }
            }
        }
        (tmp_path / "courses_geo.json").write_text(json.dumps(data))
        result = load_courses_geo()
        assert result["Course"]["holes"]["1"]["green"] == [[[0, 0], [10, 0], [10, 10]]]

    def test_new_format_extracts_rings(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        data = {
            "Course": {
                "splits": {"1": [[0, 0], [1, 1]]},
                "holes": {
                    "7": {
                        "green": [
                            {"id": "way/501234__0", "rings": [[[0, 0], [10, 0], [10, 10]]]},
                            {"id": "way/502345", "rings": [[[5, 5], [15, 5], [15, 15]]]},
                        ]
                    }
                }
            }
        }
        (tmp_path / "courses_geo.json").write_text(json.dumps(data))
        result = load_courses_geo()
        green = result["Course"]["holes"]["7"]["green"]
        assert green == [[[[0, 0], [10, 0], [10, 10]]], [[[5, 5], [15, 5], [15, 15]]]]

    def test_load_courses_geo_preserves_tee_boxes(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        data = {
            "Course": {
                "holes": {
                    "1": {"tee_boxes": {"white": [0, 0], "blue": [1, 1]}}
                }
            }
        }
        (tmp_path / "courses_geo.json").write_text(json.dumps(data))
        result = load_courses_geo()
        assert result["Course"]["holes"]["1"]["tee_boxes"] == {"white": [0, 0], "blue": [1, 1]}

    def test_handles_empty_hole(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        data = {"Course": {"holes": {"1": {}}}}
        (tmp_path / "courses_geo.json").write_text(json.dumps(data))
        result = load_courses_geo()
        hole = result["Course"]["holes"]["1"]
        assert hole == {}


class TestLoadCoursesGeoRaw:
    """load_courses_geo_raw — preserves IDs and splits."""

    def test_preserves_ids(self, monkeypatch, tmp_path):
        monkeypatch.setattr("cartographer.data._get_plugin_data_dir", lambda: tmp_path)
        data = {
            "Course": {
                "splits": {"1": [[0, 0], [1, 1]]},
                "holes": {
                    "7": {
                        "green": [
                            {"id": "way/501234__0", "rings": [[[0, 0], [10, 0], [10, 10]]]},
                        ]
                    }
                }
            }
        }
        (tmp_path / "courses_geo.json").write_text(json.dumps(data))
        result = load_courses_geo_raw()
        assert result["Course"]["splits"] == {"1": [[0, 0], [1, 1]]}
        green = result["Course"]["holes"]["7"]["green"]
        assert green[0]["id"] == "way/501234__0"
        assert green[0]["rings"] == [[[0, 0], [10, 0], [10, 10]]]
