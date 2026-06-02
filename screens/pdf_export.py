# plugins/cartographer/screens/pdf_export.py
"""PDF export screen for Cartographer plugin."""
from __future__ import annotations

import logging
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Center, Vertical, VerticalScroll
from textual.screen import Screen, ModalScreen
from textual.widgets import Header, Footer, Input, LoadingIndicator, SelectionList, Static, Button, Select, Checkbox
from textual.binding import Binding

from data import load_settings, save_settings

_log = logging.getLogger("pinsheet")


class PDFExportScreen(Screen):
    """Screen for configuring and generating PDF yardage books."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def __init__(self, course_name: str) -> None:
        super().__init__()
        self.course_name = course_name
        self.slot1_mode = "green_grid"
        self.slot2_mode = "stats_panel"
        self.show_calculated_stats = True
        self._generated = False

        import sys
        if getattr(sys, "frozen", False):
            data_dir = Path(sys.executable).parent / "data"
        else:
            data_dir = Path(__file__).parent.parent.parent.parent / "data"
        safe_name = self.course_name.lower().replace(" ", "_").replace("'", "").replace('"', "")
        self.output_dir = data_dir / "plugins" / "cartographer" / "yardage_books" / safe_name

    def compose(self) -> ComposeResult:
        """Compose the export screen UI."""
        yield Header()
        with Vertical(id="pdf-export-container"):
            with VerticalScroll(id="pdf-export-content", can_focus=False):
                yield Static(f"Export PDF: {self.course_name}", id="pdf-export-header")

                with Vertical(id="slot-section", classes="pdf-section"):
                    yield Center(
                        Vertical(
                            Static("Top slot:"),
                            Select(
                                [
                                    ("Green Grid", "green_grid"),
                                    ("Stats Panel", "stats_panel"),
                                    ("Notes", "notes"),
                                ],
                                value="green_grid",
                                id="slot1-select",
                            ),
                            Static("Bottom slot:"),
                            Select(
                                [
                                    ("Green Grid", "green_grid"),
                                    ("Stats Panel", "stats_panel"),
                                    ("Notes", "notes"),
                                ],
                                value="stats_panel",
                                id="slot2-select",
                            ),
                            classes="section-inner",
                        )
                    )

                with Vertical(id="options-section", classes="pdf-section"):
                    yield Center(
                        Vertical(
                            Checkbox("Show calculated stats", value=True, id="stats-checkbox"),
                            Checkbox("Yardage arcs", value=True, id="arcs-checkbox"),
                            Checkbox("Green: Heightmap", value=True, id="heightmap-checkbox"),
                            Checkbox("Green: Contours", value=True, id="contours-checkbox"),
                            Checkbox("Green: Arrows", value=True, id="arrows-checkbox"),
                            Static("Available tees:", id="tees-label"),
                            SelectionList(id="tees-selection"),
                            Static("Output directory:"),
                            Input(value=str(self.output_dir), id="output-dir-input", disabled=True),
                            classes="section-inner",
                        )
                    )

                yield Center(
                    Button("Generate PDF", variant="primary", id="generate-button")
                )

            yield Static("", id="status-widget")
            yield Static("", id="status-detail")
            yield LoadingIndicator(id="loading-indicator")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"PDF Export — {self.course_name}"
        _log.info("screen: pdf_export %s", self.course_name)

        import json
        import sys
        from pathlib import Path

        settings = load_settings()
        self.query_one("#arcs-checkbox", Checkbox).value = settings.get("cartographer.yardage_arcs", True)
        self.query_one("#heightmap-checkbox", Checkbox).value = settings.get("cartographer.green_heightmap", True)
        self.query_one("#contours-checkbox", Checkbox).value = settings.get("cartographer.green_contours", True)
        self.query_one("#arrows-checkbox", Checkbox).value = settings.get("cartographer.green_arrows", True)

        if getattr(sys, "frozen", False):
            courses_json = Path(sys.executable).parent / "data" / "courses.json"
        else:
            courses_json = Path(__file__).parent.parent.parent.parent / "data" / "courses.json"

        self.selected_tees: list[str] = []
        if courses_json.exists():
            courses = json.loads(courses_json.read_text())
            course_data = courses.get(self.course_name, {})
            tees = sorted(course_data.get("tees", {}).keys())
            if tees:
                tees_sel = self.query_one("#tees-selection", SelectionList)
                for tee in tees:
                    tees_sel.add_option((tee, tee, True))
                self.selected_tees = list(tees)

        self.query_one("#loading-indicator", LoadingIndicator).display = False
        self.query_one("#slot-section").border_title = "Slot Content"
        self.query_one("#options-section").border_title = "Options"

    def on_screen_resume(self) -> None:
        """Refresh settings state when returning to this screen."""
        settings = load_settings()
        self.query_one("#arcs-checkbox", Checkbox).value = settings.get("cartographer.yardage_arcs", True)
        self.query_one("#heightmap-checkbox", Checkbox).value = settings.get("cartographer.green_heightmap", True)
        self.query_one("#contours-checkbox", Checkbox).value = settings.get("cartographer.green_contours", True)
        self.query_one("#arrows-checkbox", Checkbox).value = settings.get("cartographer.green_arrows", True)

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        """Handle tee selection changes."""
        if event.selection_list.id == "tees-selection":
            self.selected_tees = list(event.selection_list.selected)

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle slot selector changes."""
        if event.select.id == "slot1-select":
            self.slot1_mode = event.value
        elif event.select.id == "slot2-select":
            self.slot2_mode = event.value

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Handle checkbox toggles."""
        if event.checkbox.id == "stats-checkbox":
            self.show_calculated_stats = event.value
        elif event.checkbox.id == "arcs-checkbox":
            data = load_settings()
            data["cartographer.yardage_arcs"] = event.value
            save_settings(data)
            _log.info("setting changed: cartographer.yardage_arcs = %s", event.value)
        elif event.checkbox.id == "heightmap-checkbox":
            data = load_settings()
            data["cartographer.green_heightmap"] = event.value
            save_settings(data)
            _log.info("setting changed: cartographer.green_heightmap = %s", event.value)
        elif event.checkbox.id == "contours-checkbox":
            data = load_settings()
            data["cartographer.green_contours"] = event.value
            save_settings(data)
            _log.info("setting changed: cartographer.green_contours = %s", event.value)
        elif event.checkbox.id == "arrows-checkbox":
            data = load_settings()
            data["cartographer.green_arrows"] = event.value
            save_settings(data)
            _log.info("setting changed: cartographer.green_arrows = %s", event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle generate button press."""
        if event.button.id != "generate-button":
            return

        _log.info("pdf_export: generate requested for %s", self.course_name)

        # Validate geometry data exists
        from ..data import load_courses_geo
        courses_geo = load_courses_geo()
        if self.course_name not in courses_geo:
            self.app.push_screen(
                ErrorModal("No geometry data found for this course.\nRun Geometry Setup (g) first.")
            )
            return

        # Validate course data exists
        import sys
        import json
        if getattr(sys, "frozen", False):
            courses_json = Path(sys.executable).parent / "data" / "courses.json"
        else:
            courses_json = Path(__file__).parent.parent.parent.parent / "data" / "courses.json"

        if not courses_json.exists():
            self.app.push_screen(
                ErrorModal("Course data not found in PinSheet.")
            )
            return

        pinsheet_courses = json.loads(courses_json.read_text())
        if self.course_name not in pinsheet_courses:
            self.app.push_screen(
                ErrorModal(f"Course '{self.course_name}' not found in PinSheet data.")
            )
            return

        # Start generation
        self.query_one("#generate-button", Button).disabled = True
        self.query_one("#loading-indicator", LoadingIndicator).display = True
        self._generate_pdf()

    @work(thread=True)
    def _generate_pdf(self) -> None:
        """Generate PDF in background thread with progress updates."""
        _log.info("pdf_export: generating for %s", self.course_name)

        from ..pdf import generate_book

        settings = load_settings()

        def progress_callback(current: int, total: int) -> None:
            if current <= 20:
                msg = f"Exporting sheet {current}/20..."
            else:
                msg = f"Combining booklet {current - 20}/5..."
            self.app.call_from_thread(self._update_status, msg)

        def status_callback(msg: str) -> None:
            self.app.call_from_thread(self._update_status_detail, msg)

        try:
            # TODO: Pass self.selected_tees as a tees filter once generate_book supports it
            generate_book(
                course_name=self.course_name,
                output_dir=self.output_dir,
                slot1_mode=self.slot1_mode,
                slot2_mode=self.slot2_mode,
                show_calculated_stats=self.show_calculated_stats,
                settings={
                    "cartographer.yardage_arcs": settings.get("cartographer.yardage_arcs", True),
                    "cartographer.yardage_arc_distances": settings.get("cartographer.yardage_arc_distances", [100, 125, 150]),
                    "cartographer.green_heightmap": settings.get("cartographer.green_heightmap", True),
                    "cartographer.green_contours": settings.get("cartographer.green_contours", True),
                    "cartographer.green_arrows": settings.get("cartographer.green_arrows", True),
                },
                progress_callback=progress_callback,
                status_callback=status_callback,
            )
            self.app.call_from_thread(self._update_status, f"PDF generated: {self.output_dir}")
            self.app.call_from_thread(self._on_success)
            self.app.call_from_thread(self.app.notify, "PDF generated successfully", severity="information")
            _log.info("pdf_export: generation complete for %s", self.course_name)
        except Exception as e:
            msg = self._format_error(e)
            self.app.call_from_thread(self._on_failure, msg)
            self.app.call_from_thread(self.app.notify, "PDF generation failed", severity="error")
            _log.exception("pdf_export: generation failed for %s", self.course_name)

    def _format_error(self, error: Exception) -> str:
        """Return a human-readable error message based on exception type."""
        if isinstance(error, ImportError):
            name = getattr(error, "name", None) or "unknown module"
            return f"Missing dependency: {name}. Install it with pip install {name}"
        elif isinstance(error, FileNotFoundError):
            path = error.filename or str(error)
            return f"File not found: {path}"
        elif isinstance(error, PermissionError):
            path = error.filename or str(error)
            return f"Permission denied: {path}"
        elif isinstance(error, OSError) and "No space left on device" in str(error):
            return "Disk full or write error"
        else:
            return f"PDF generation failed: {error}"

    def _on_success(self) -> None:
        """Handle successful PDF generation."""
        self._generated = True
        self.query_one("#generate-button", Button).label = "Regenerate"
        self._on_complete()

    def _on_failure(self, message: str) -> None:
        """Handle failed PDF generation."""
        self._generated = False
        self.query_one("#generate-button", Button).label = "Retry"
        self._show_error(message)
        self._on_complete()

    def _update_status(self, message: str) -> None:
        """Update primary status widget from main thread."""
        status = self.query_one("#status-widget", Static)
        status.remove_class("status-error")
        status.update(message)

    def _update_status_detail(self, message: str) -> None:
        """Update granular status widget from main thread."""
        detail = self.query_one("#status-detail", Static)
        detail.update(message)

    def _show_error(self, message: str) -> None:
        """Show error in status widget and push error modal."""
        status = self.query_one("#status-widget", Static)
        status.update(message)
        status.add_class("status-error")
        self.app.push_screen(ErrorModal(message))

    def _on_complete(self) -> None:
        """Re-enable UI after generation completes or fails."""
        self.query_one("#loading-indicator", LoadingIndicator).display = False
        self.query_one("#generate-button", Button).disabled = False


class ErrorModal(ModalScreen[bool]):
    """Simple error modal."""

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="error-modal"):
            yield Static(self.message, id="error-message")
            yield Button("OK", variant="primary", id="error-ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "error-ok":
            self.dismiss(True)
