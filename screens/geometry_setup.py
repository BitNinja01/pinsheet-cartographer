"""GeometrySetupScreen — guide user through .osm file setup and tagger launch."""
from __future__ import annotations

import shutil
import threading
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static


class GeometrySetupScreen(Screen):
    """Guide through .osm file acquisition and launch the browser tagger."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
    ]

    _shutdown_event: threading.Event | None = None
    _tagger_running: bool = False
    _cancelled: bool = False

    def __init__(self, course_name: str, **kwargs):
        super().__init__(**kwargs)
        self._course_name = course_name

    def compose(self) -> ComposeResult:
        yield Header()

        with Center():
            with Vertical(id="geometry-setup-form"):
                yield Label(
                    "Export your course from [bold]openstreetmap.org[/bold].\n"
                    "Draw a bounding box around the course on the map,\n"
                    "click Export, and download the [bold].osm[/bold] file.",
                    id="geometry-instructions",
                )
                yield Label("", id="geometry-existing-note")
                yield Label("Paste the path to the .osm file:", id="geometry-path-label")
                yield Input(
                    placeholder="/home/user/Downloads/Maplewood.osm",
                    id="geometry-path-input",
                )
                yield Label("", id="geometry-error")
                yield Button("Start", variant="primary", id="geometry-start-btn")
            yield Static("", id="geometry-status")

        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Course Geometry — {self._course_name}"
        self.query_one("#geometry-status").display = False
        self._check_existing_geometry()

    def _check_existing_geometry(self) -> None:
        try:
            from ..data import load_courses_geo
        except ImportError:
            return

        courses_geo = load_courses_geo()
        if self._course_name in courses_geo:
            self.query_one("#geometry-existing-note", Label).update(
                "Geometry for this course already exists.\n"
                "Starting the tagger will overwrite the current assignments."
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "geometry-start-btn":
            return
        if self._tagger_running:
            return

        path = self.query_one("#geometry-path-input", Input).value.strip()
        self.query_one("#geometry-error", Label).update("")

        # Validate
        if not path:
            self.query_one("#geometry-error", Label).update(
                "[bold red]Please enter a file path.[/]"
            )
            return

        osm_file = Path(path).expanduser()
        if not osm_file.exists():
            self.query_one("#geometry-error", Label).update(
                f"[bold red]File not found:[/] {osm_file}"
            )
            return

        if osm_file.is_dir():
            self.query_one("#geometry-error", Label).update(
                "[bold red]Path is a directory, not a file.[/]"
            )
            return

        self._start_tagger(osm_file)

    def _start_tagger(self, osm_file: Path) -> None:
        try:
            from ..data import get_osm_path
            from ..tagger.server import start_tagger
        except ImportError as e:
            self.query_one("#geometry-error", Label).update(
                f"[bold red]Could not import tagger:[/] {e}"
            )
            return

        # Copy file to cache location
        osm_cache_path = get_osm_path(self._course_name)
        try:
            shutil.copy(osm_file, osm_cache_path)
        except OSError as e:
            self.query_one("#geometry-error", Label).update(
                f"[bold red]Could not copy file:[/] {e}"
            )
            return

        # Hide form widgets, show running state
        self._tagger_running = True
        for widget_id in ("geometry-instructions", "geometry-existing-note",
                          "geometry-path-label", "geometry-path-input",
                          "geometry-error", "geometry-start-btn"):
            self.query_one(f"#{widget_id}").display = False

        self.query_one("#geometry-status", Static).display = True
        self.query_one("#geometry-status", Static).update(
            "Tagger is running at [bold]http://localhost:5173[/bold]\n\n"
            "A browser should have opened. Click a feature on\n"
            "the map to assign it to a hole. Use Set Scale to\n"
            "set two reference points with a known distance.\n"
            "Press [bold]Save[/bold] when done."
        )

        try:
            shutdown_event = start_tagger(self._course_name, osm_cache_path)
        except Exception as e:
            self.query_one("#geometry-status", Static).update(
                f"[bold red]Could not start tagger:[/] {e}"
            )
            self._tagger_running = False
            return

        self._shutdown_event = shutdown_event
        self._poll_url = "http://localhost:5173"
        self._poll_count = 0
        self.set_interval(0.5, self._poll_tagger)

    def _poll_tagger(self) -> None:
        self._poll_count += 1
        dots = "." * ((self._poll_count // 2) % 4)

        if self._shutdown_event is None:
            return

        if self._shutdown_event.is_set():
            if self._cancelled:
                return
            # Tagger saved — navigate to gallery
            self._shutdown_event = None
            try:
                from .course_gallery import CourseGalleryScreen
            except ImportError:
                self.app.pop_screen()
                return
            self.app.switch_screen(CourseGalleryScreen(self._course_name))
            return

        self.query_one("#geometry-status", Static).update(
            "Tagger is running at [bold]http://localhost:5173[/bold]\n\n"
            "A browser should have opened. Click a feature on\n"
            "the map to assign it to a hole. Use Set Scale to\n"
            "set two reference points with a known distance.\n"
            "Press [bold]Save[/bold] when done.\n\n"
            f"Waiting for tagger to complete{dots}"
        )

    def action_back(self) -> None:
        if self._tagger_running and self._shutdown_event is not None:
            self._cancelled = True
            self._shutdown_event.set()
            self._shutdown_event = None
        self.app.pop_screen()
