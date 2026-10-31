from collections.abc import Callable

from gi.repository import Gtk, Pango


class JobProgress:
    """Bar that slides in above the grid: "Importing 12 of 300: Title", progress, Cancel."""

    def __init__(self, on_cancel: Callable[[], None]) -> None:
        self._label = Gtk.Label(
            xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE, css_classes=["caption"]
        )
        self._bar = Gtk.ProgressBar(hexpand=True)
        self._cancel_btn = Gtk.Button(label="Cancel", valign=Gtk.Align.CENTER)
        self._cancel_btn.connect("clicked", lambda _btn: self._cancel(on_cancel))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        text.append(self._label)
        text.append(self._bar)
        row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_start=16,
            margin_end=16,
            margin_top=10,
            margin_bottom=10,
        )
        row.append(text)
        row.append(self._cancel_btn)

        self.widget = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, child=row, reveal_child=False
        )

    def show(self, done: int, total: int, current: str) -> None:
        """`done` finished so far; `current` is the item being worked on now."""
        self._label.set_text(f"Importing {done + 1} of {total}: {current}")
        self._bar.set_fraction(done / total if total else 0)
        self._cancel_btn.set_sensitive(True)
        self.widget.set_reveal_child(True)

    def show_message(self, text: str) -> None:
        """Before the total is known, e.g. while looking for files."""
        self._label.set_text(text)
        self._bar.set_fraction(0)
        self._cancel_btn.set_sensitive(True)
        self.widget.set_reveal_child(True)

    def hide(self) -> None:
        self.widget.set_reveal_child(False)

    def _cancel(self, on_cancel: Callable[[], None]) -> None:
        self._label.set_text("Cancelling after the current file…")
        self._cancel_btn.set_sensitive(False)
        on_cancel()
