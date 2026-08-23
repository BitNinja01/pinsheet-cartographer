"""Tests for canonical PinSheet course-data reads in the PDF pipeline."""

from cartographer.pdf import hole_tee_yardages, _hole_par_and_index


CANONICAL_COURSE = {
    "par": 72,
    "location": {"city": "Auburn", "state": "WA", "country": "USA"},
    "holes": {
        "1": {"par": 5, "hole_index": 11},
        "2": {"par": 4, "hole_index": 3},
    },
    "tees": {
        "Husky": {"rating": 75.5, "slope": 143, "yardage": 7304,
                  "yardages": {"1": "560", "2": "465"}},
        "Senior": {"rating": 72.7, "slope": 139, "yardage": 6772,
                   "yardages": {"1": "526", "2": ""}},
        "Junior": {"rating": 71.1, "slope": 136, "yardage": 6420},
    },
}


def test_hole_tee_yardages_reads_canonical_tee_level_yardages():
    assert hole_tee_yardages(CANONICAL_COURSE, "1") == {"Husky": 560, "Senior": 526}
    assert hole_tee_yardages(CANONICAL_COURSE, "2") == {"Husky": 465}


def test_hole_tee_yardages_skips_missing_or_blank():
    # "Junior" has no yardages dict at all; Senior hole 2 is blank.
    assert "Junior" not in hole_tee_yardages(CANONICAL_COURSE, "1")
    assert "Senior" not in hole_tee_yardages(CANONICAL_COURSE, "2")


def test_hole_tee_yardages_returns_empty_for_unknown_hole():
    assert hole_tee_yardages(CANONICAL_COURSE, "18") == {}


def test_hole_par_and_index_reads_canonical_hole_index():
    assert _hole_par_and_index(CANONICAL_COURSE, 1) == (5, 11)
    assert _hole_par_and_index(CANONICAL_COURSE, 2) == (4, 3)


def test_hole_par_and_index_defaults_when_hole_missing():
    assert _hole_par_and_index(CANONICAL_COURSE, 18) == (4, 18)
