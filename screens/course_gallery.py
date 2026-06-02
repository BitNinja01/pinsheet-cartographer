# plugins/cartographer/screens/course_gallery.py
"""CourseGalleryScreen — browse all hole diagrams for a course."""
from __future__ import annotations

import tempfile
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, LoadingIndicator, Static


class CourseGalleryScreen(Screen):
    """Browse all 18 hole diagrams for a course with a sidebar navigator."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("j", "next_hole", "Next Hole"),
        Binding("k", "prev_hole", "Prev Hole"),
    ]

    def __init__(self, course_name: str, **kwargs):
        super().__init__(**kwargs)
        self._course_name = course_name
        self._current_hole = 1
        self._png_cache: dict[int, Path | None] = {}

    def compose(self) -> ComposeResult:
        from textual_image.widget import TGPImage as TImage

        yield Header()
        with Horizontal(id="gallery-layout"):
            with ListView(id="gallery-sidebar"):
                for h in range(1, 19):
                    yield ListItem(Static(f"Hole {h:2d}"), id=f"hole-item-{h}")
            with Vertical(id="gallery-content"):
                yield LoadingIndicator(id="gallery-loading")
                yield TImage(id="gallery-svg")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Course Gallery — {self._course_name}"
        self._load_hole(1)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        if item_id.startswith("hole-item-"):
            hole_num = int(item_id.split("-")[-1])
            self._current_hole = hole_num
            self._load_hole(hole_num)

    def action_next_hole(self) -> None:
        if self._current_hole < 18:
            self._current_hole += 1
            self._load_hole(self._current_hole)
            self.query_one(f"#hole-item-{self._current_hole}").scroll_visible()

    def action_prev_hole(self) -> None:
        if self._current_hole > 1:
            self._current_hole -= 1
            self._load_hole(self._current_hole)
            self.query_one(f"#hole-item-{self._current_hole}").scroll_visible()

    def _load_hole(self, hole_num: int) -> None:
        if hole_num in self._png_cache:
            self._display_svg(hole_num, self._png_cache[hole_num])
            return
        self.query_one("#gallery-loading").display = True
        self.query_one("#gallery-svg").display = False
        self._render_hole_worker(hole_num)

    @work(thread=True)
    def _render_hole_worker(self, hole_num: int) -> None:
        from textual_image.widget import get_cell_size
        from ..renderer import render_hole_svg, svg_to_png
        from ..data import load_courses_geo

        courses_geo = load_courses_geo()
        if self._course_name not in courses_geo:
            self._png_cache[hole_num] = None
            self.app.call_from_thread(self._display_svg, hole_num, None)
            return

        svg = render_hole_svg(self._course_name, hole_num)
        if not svg:
            self._png_cache[hole_num] = None
            self.app.call_from_thread(self._display_svg, hole_num, None)
            return

        try:
            cell_w, cell_h = get_cell_size()
        except Exception:
            cell_w, cell_h = 8, 16

        available_cols = self.size.width
        available_rows = self.size.height - 2
        target_w = available_cols * cell_w
        target_h = available_rows * cell_h

        try:
            png_bytes = svg_to_png(svg, target_w, target_h)
        except Exception:
            self._png_cache[hole_num] = None
            self.app.call_from_thread(self._display_svg, hole_num, None)
            return

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(png_bytes)
        tmp.close()
        png_path = Path(tmp.name)
        self._png_cache[hole_num] = png_path
        self.app.call_from_thread(self._display_svg, hole_num, png_path)

    def _display_svg(self, hole_num: int, png_path: Path | None) -> None:
        from textual_image.widget import TGPImage as TImage

        self.query_one("#gallery-loading").display = False
        widget = self.query_one("#gallery-svg", TImage)

        if png_path is None:
            widget.image = None
            self.notify(
                f"No course geometry available for {self._course_name}.\n"
                f'Run: python -m cartographer.tagger "{self._course_name}" to add it.',
                severity="error", timeout=8,
            )
        else:
            widget.image = png_path

        widget.display = True
