from cartographer.layout import (
    flip_page_svg, compose_sheet, render_hole_page, render_bottom_slots,
    compose_front_page, compose_back_page, compose_chart_page, compose_notes_page,
    PAGE_W, PAGE_CONTENT_H, PAGE_H, MARGIN, PRINTABLE_W, SLOT_H,
)

EMPTY_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 270 486" width="270pt" height="486pt"><rect width="270" height="486" fill="white"/></svg>'


class TestComposeFrontPage:
    def test_returns_string(self):
        result = compose_front_page("Test Course")
        assert isinstance(result, str)

    def test_starts_with_svg(self):
        result = compose_front_page("Test Course")
        assert result.startswith("<svg")

    def test_contains_course_name(self):
        result = compose_front_page("Bellevue GC")
        assert "Bellevue GC" in result

    def test_contains_par(self):
        result = compose_front_page("Course", total_par=72)
        assert "Par 72" in result

    def test_viewbox(self):
        result = compose_front_page("Course")
        assert f'viewBox="0 0 {PAGE_W} {PAGE_CONTENT_H}"' in result

    def test_with_location(self):
        location = {"city": "Bellevue", "state": "WA", "country": "USA"}
        result = compose_front_page("Course", location=location)
        assert "Bellevue" in result
        assert "WA" in result
        assert "USA" in result

    def test_with_tee_totals(self):
        tee_totals = {"blue": 6200, "white": 5800}
        result = compose_front_page("Course", tee_totals=tee_totals)
        assert "BLUE" in result
        assert "6200" in result
        assert "WHITE" in result
        assert "5800" in result


class TestComposeBackPage:
    def test_returns_svg_string(self):
        result = compose_back_page()
        assert isinstance(result, str)

    def test_starts_with_svg(self):
        result = compose_back_page()
        assert result.startswith("<svg")

    def test_contains_pinsheet(self):
        result = compose_back_page()
        assert "PinSheet" in result

    def test_without_overview_svg(self):
        result = compose_back_page()
        assert f'viewBox="0 0 {PAGE_W} {PAGE_CONTENT_H}"' in result

    def test_with_overview_svg(self):
        result = compose_back_page(full_course_svg=EMPTY_SVG)
        assert f'viewBox="0 0 {PAGE_W} {PAGE_CONTENT_H}"' in result
        assert "PinSheet" in result


class TestComposeChartPage:
    def test_returns_svg_string(self):
        result = compose_chart_page()
        assert isinstance(result, str)

    def test_starts_with_svg(self):
        result = compose_chart_page()
        assert result.startswith("<svg")

    def test_default_title(self):
        result = compose_chart_page()
        assert "Club Distances" in result

    def test_custom_title(self):
        result = compose_chart_page(title="Custom Chart")
        assert "Custom Chart" in result

    def test_contains_table_headers(self):
        result = compose_chart_page()
        assert "Club" in result
        assert "Carry" in result
        assert "Half" in result
        assert "Max" in result

    def test_viewbox(self):
        result = compose_chart_page()
        assert f'viewBox="0 0 {PAGE_W} {PAGE_CONTENT_H}"' in result


class TestComposeNotesPage:
    def test_returns_svg_string(self):
        result = compose_notes_page()
        assert isinstance(result, str)

    def test_starts_with_svg(self):
        result = compose_notes_page()
        assert result.startswith("<svg")

    def test_contains_lines(self):
        result = compose_notes_page()
        assert "<line" in result

    def test_viewbox(self):
        result = compose_notes_page()
        assert f'viewBox="0 0 {PAGE_W} {PAGE_CONTENT_H}"' in result


class TestFlipPageSvg:
    def test_returns_svg_string(self):
        result = flip_page_svg(EMPTY_SVG, 270, 486)
        assert isinstance(result, str)

    def test_starts_with_svg(self):
        result = flip_page_svg(EMPTY_SVG, 270, 486)
        assert result.startswith("<svg")

    def test_contains_rotation(self):
        result = flip_page_svg(EMPTY_SVG, 270, 486)
        assert "rotate(180" in result

    def test_contains_image(self):
        result = flip_page_svg(EMPTY_SVG, 270, 486)
        assert "<image" in result


class TestComposeSheet:
    def test_returns_svg_string(self):
        result = compose_sheet(EMPTY_SVG, EMPTY_SVG)
        assert isinstance(result, str)

    def test_starts_with_svg(self):
        result = compose_sheet(EMPTY_SVG, EMPTY_SVG)
        assert result.startswith("<svg")

    def test_contains_page_h_in_viewbox(self):
        result = compose_sheet(EMPTY_SVG, EMPTY_SVG)
        assert f'viewBox="0 0 {PAGE_W} {PAGE_H}"' in result

    def test_contains_image_elements(self):
        result = compose_sheet(EMPTY_SVG, EMPTY_SVG)
        assert result.count("<image") == 2

    def test_contains_white_background(self):
        result = compose_sheet(EMPTY_SVG, EMPTY_SVG)
        assert "<rect" in result


class TestRenderHolePage:
    def test_returns_svg_string(self):
        result = render_hole_page(EMPTY_SVG, 1, 4, {})
        assert isinstance(result, str)

    def test_starts_with_svg(self):
        result = render_hole_page(EMPTY_SVG, 7, 3, {})
        assert result.startswith("<svg")

    def test_contains_hole_number(self):
        result = render_hole_page(EMPTY_SVG, 7, 3, {})
        assert "7" in result

    def test_contains_par(self):
        result = render_hole_page(EMPTY_SVG, 1, 5, {})
        assert "Par 5" in result

    def test_contains_tee_yardages_when_provided(self):
        tee_yardages = {"blue": 420, "white": 400}
        result = render_hole_page(EMPTY_SVG, 1, 4, tee_yardages)
        assert "BLUE" in result
        assert "420" in result
        assert "WHITE" in result
        assert "400" in result

    def test_no_tee_yardages_section_when_empty(self):
        result = render_hole_page(EMPTY_SVG, 1, 4, {})
        assert "BLUE" not in result
        assert "WHITE" not in result


class TestRenderBottomSlots:
    def test_returns_svg_string(self):
        result = render_bottom_slots("green_grid", "notes", slot1_svg=EMPTY_SVG)
        assert isinstance(result, str)

    def test_starts_with_svg(self):
        result = render_bottom_slots("green_grid", "notes", slot1_svg=EMPTY_SVG)
        assert result.startswith("<svg")

    def test_viewbox(self):
        result = render_bottom_slots("green_grid", "notes", slot1_svg=EMPTY_SVG)
        assert f'viewBox="0 0 {PAGE_W} {PAGE_CONTENT_H}"' in result

    def test_green_grid_slot_contains_image(self):
        result = render_bottom_slots("green_grid", "notes", slot1_svg=EMPTY_SVG)
        assert "<image" in result

    def test_stats_panel_with_data(self):
        stats_data = {
            1: {
                "fairway_misses": "L 60% \u00b7 R 40%",
                "gir_misses": "S 50% \u00b7 LO 25%",
                "benchmark": "Avg: 4.3 \u00b7 Exp: 4.8",
                "penalties": "0.3 avg",
            }
        }
        result = render_bottom_slots("stats_panel", "notes", stats_data=stats_data, hole_num=1)
        assert "FAIRWAY MISSES" in result
        assert "GIR MISSES" in result
        assert "SCORE" in result
        assert "PENALTIES" in result

    def test_works_with_empty_slots(self):
        result = render_bottom_slots("notes", "notes")
        assert result.startswith("<svg")
