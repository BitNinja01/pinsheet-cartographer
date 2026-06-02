# plugins/cartographer/screens/hole_view.py
"""HoleViewScreen — single hole diagram view for PinSheet."""
from __future__ import annotations

import tempfile
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header


class HoleViewScreen(Screen):
    """Shows the hole diagram for a single hole of a course."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def __init__(self, course_name: str, hole_number: int, **kwargs):
        super().__init__(**kwargs)
        self._course_name = course_name
        self._hole_number = hole_number

    def compose(self) -> ComposeResult:
        from textual_image.widget import TGPImage as TImage

        yield Header()
        yield TImage(id="hole-svg-widget")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Hole {self._hole_number} — {self._course_name}"
        self._render_hole()

    @work(thread=True)
    def _render_hole(self) -> None:
        from textual_image.widget import get_cell_size
        from ..renderer import render_hole_svg, svg_to_png
        from ..data import load_courses_geo

        courses_geo = load_courses_geo()
        if self._course_name not in courses_geo:
            self.app.call_from_thread(self._display_result, None)
            return

        svg = render_hole_svg(self._course_name, self._hole_number)
        if not svg:
            self.app.call_from_thread(self._display_result, None)
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
            self.app.call_from_thread(self._display_result, None)
            return

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(png_bytes)
        tmp.close()

        self.app.call_from_thread(self._display_result, Path(tmp.name))

    def _display_result(self, png_path: Path | None) -> None:
        from textual_image.widget import TGPImage as TImage

        widget = self.query_one("#hole-svg-widget", TImage)
        if png_path is None:
            widget.update(
                f"No course geometry available for {self._course_name}.\n\n"
                f'Run: python -m cartographer.tagger "{self._course_name}" to add it.'
            )
        else:
            widget.image = png_path
