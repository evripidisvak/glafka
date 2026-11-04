"""The History page: what was sent where, grouped by day, newest first."""

from collections.abc import Callable
from datetime import date, timedelta

from gi.repository import Adw, Gtk

from glafka.core.models import Book, Destination, HistoryAction, HistoryEntry
from glafka.ui.book_card import cover_widget

# (label, kind) filters before the per-destination ones
_KIND_FILTERS = [("All Destinations", None), ("Copied by USB", "usb"), ("Emailed", "email")]


def describe(entry: HistoryEntry) -> tuple[str, str]:
    """('Copied to Era Lite', icon name) for an entry."""
    name = entry.destination_name
    match entry.action:
        case HistoryAction.SENT if entry.destination_kind == "email":
            return f"Emailed to {name}", "mail-send-symbolic"
        case HistoryAction.SENT:
            return f"Copied to {name}", "media-removable-symbolic"
        case HistoryAction.ALREADY_THERE:
            return f"Already on {name}", "media-removable-symbolic"
        case HistoryAction.DELETED:
            return f"Deleted from {name}", "user-trash-symbolic"
        case HistoryAction.MARKED_REMOVED:
            return f"Marked as removed from {name}", "edit-clear-symbolic"
    return f"{entry.action} {name}", "document-open-recent-symbolic"


def details(entry: HistoryEntry, with_date: bool) -> str:
    """'EPUB · 31 Oct 19:42' (or just the time, under a day header)."""
    local = entry.at.astimezone()
    when = local.strftime("%-d %b %H:%M") if with_date else local.strftime("%H:%M")
    return f"{entry.format.upper()} · {when}" if entry.format else when


def day_label(day: date, today: date) -> str:
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return day.strftime("%-d %B") if day.year == today.year else day.strftime("%-d %B %Y")


class HistoryView:
    """Rebuilt on demand by the window (it is not a list model: entries aren't BookObjects)."""

    def __init__(self, on_activate: Callable[[str], None]) -> None:
        self._on_activate = on_activate
        self._entries: list[HistoryEntry] = []
        self._books: dict[str, Book] = {}
        self._query = ""
        self._filters: list[tuple[str, str | None, str | None]] = []  # (label, kind, dest id)
        self._replacing_filters = False  # set_model and set_selected each notify

        self._filter = Gtk.DropDown(halign=Gtk.Align.START)
        self._filter.connect("notify::selected", lambda *_args: self._rebuild())
        self._sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self._empty = Adw.StatusPage(icon_name="document-open-recent-symbolic")

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=18,
            margin_bottom=24,
            margin_start=12,
            margin_end=12,
        )
        content.append(self._filter)
        content.append(self._sections)
        content.append(self._empty)
        self.widget = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
            child=Adw.Clamp(maximum_size=720, child=content),
        )

    def set_entries(
        self,
        entries: list[HistoryEntry],
        books: dict[str, Book],
        destinations: list[Destination],
        query: str,
    ) -> None:
        self._entries, self._books, self._query = entries, books, query.strip().casefold()
        chosen = self._filters[self._filter.get_selected()] if self._filters else None
        self._filters = [(label, kind, None) for label, kind in _KIND_FILTERS] + [
            (d.name, None, d.id) for d in destinations
        ]
        labels = Gtk.StringList.new([label for label, _kind, _dest in self._filters])
        keep = next((i for i, f in enumerate(self._filters) if chosen and f[1:] == chosen[1:]), 0)
        self._replacing_filters = True
        self._filter.set_model(labels)  # resets the selection; restored below
        self._filter.set_selected(keep)
        self._replacing_filters = False
        self._rebuild()

    def _shown(self) -> list[HistoryEntry]:
        _label, kind, dest_id = self._filters[self._filter.get_selected()]
        shown = []
        for entry in self._entries:
            if kind is not None and entry.destination_kind != kind:
                continue
            if dest_id is not None and entry.destination_id != dest_id:
                continue
            book = self._books.get(entry.book_id) if entry.book_id else None
            title = (
                f"{book.title} {book.author}" if book else f"{entry.book_title} {entry.book_author}"
            )
            text = f"{title} {entry.destination_name}".casefold()
            if self._query and self._query not in text:
                continue
            shown.append(entry)
        return shown

    def _rebuild(self) -> None:
        if not self._filters or self._replacing_filters:
            return
        while (child := self._sections.get_first_child()) is not None:
            self._sections.remove(child)
        shown = self._shown()
        self._filter.set_visible(bool(self._entries))
        self._empty.set_visible(not shown)
        if not self._entries:
            self._empty.set_title("No History Yet")
            self._empty.set_description(
                "Books you copy to a reader or email show up here, with the date."
            )
        elif not shown:
            self._empty.set_title("Nothing Matches")
            self._empty.set_description("No history matches the search or this filter.")

        today = date.today()
        group: Adw.PreferencesGroup | None = None
        current: date | None = None
        for entry in shown:
            day = entry.at.astimezone().date()
            if day != current:
                current = day
                group = Adw.PreferencesGroup(title=day_label(day, today))
                self._sections.append(group)
            group.add(self._row(entry))

    def _row(self, entry: HistoryEntry) -> Adw.ActionRow:
        text, icon = describe(entry)
        book = self._books.get(entry.book_id) if entry.book_id else None
        subtitle = f"{text} · {details(entry, with_date=False)}"
        if book is None:
            subtitle += " · no longer in the library"
        row = Adw.ActionRow(
            title=book.title if book else entry.book_title,  # current title, if it still exists
            subtitle=subtitle,
            use_markup=False,
            activatable=book is not None,
        )
        if book is not None:
            row.add_prefix(cover_widget(book, 32, 46, with_text=False))
            row.connect("activated", lambda _row, book_id=book.id: self._on_activate(book_id))
        else:
            row.add_prefix(
                Gtk.Image(
                    icon_name="accessories-dictionary-symbolic",
                    pixel_size=24,
                    css_classes=["dim-label"],
                    width_request=32,
                )
            )
        row.add_suffix(Gtk.Image(icon_name=icon, css_classes=["dim-label"]))
        return row
