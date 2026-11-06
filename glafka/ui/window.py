import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from glafka import APP_ID, __version__
from glafka.core.config import AppConfig
from glafka.core.device import RemoveResult, ScanResult
from glafka.core.errors import RECOVERABLE_ERRORS
from glafka.core.grouping import GroupBy, group_books, sort_key
from glafka.core.importer import ImportPlan, ImportResult, ImportStatus, ItemOutcome
from glafka.core.library import FileSync, LibraryManager
from glafka.core.models import (
    Book,
    BookFilter,
    BookSort,
    Destination,
    DeviceCopy,
    HistoryAction,
)
from glafka.core.send import SendResult, SendStatus
from glafka.ui.book_card import create_book_card, create_book_row
from glafka.ui.book_object import BookObject
from glafka.ui.details_pane import DetailsPane, ReaderStatus
from glafka.ui.drives import DriveMonitor
from glafka.ui.group_view import GroupView
from glafka.ui.history_view import HistoryView, describe, details
from glafka.ui.job_progress import JobProgress
from glafka.ui.preferences import (
    DestinationSettings,
    choose_library_folder,
    show_preferences_dialog,
)
from glafka.ui.secrets import lookup_password
from glafka.ui.thumbnails import thumbnails
from glafka.ui.trash import move_to_trash
from glafka.ui.worker import Job, LibraryWorker

log = logging.getLogger(__name__)

# Sort menu choices (by, descending) -> BookSort
SORTS = {
    ("title", False): BookSort.TITLE_ASC,
    ("title", True): BookSort.TITLE_DESC,
    ("author", False): BookSort.AUTHOR_ASC,
    ("author", True): BookSort.AUTHOR_DESC,
    ("date", False): BookSort.DATE_ADDED_ASC,
    ("date", True): BookSort.DATE_ADDED_DESC,
}
# Categories with a fixed order of their own: the sort menu is disabled there
FIXED_ORDER = {
    "Recently Added": "Recently Added is always newest first",
    "History": "History is always newest first",
    "Series": "Series are in reading order",
}

# Categories shown as sections with a header per group, not as one grid
GROUPED_CATEGORIES = {"Authors": GroupBy.AUTHOR, "Series": GroupBy.SERIES, "Tags": GroupBy.TAG}

CATEGORIES = [
    ("All Books", "accessories-dictionary-symbolic"),
    ("Recently Added", "document-new-symbolic"),
    ("Authors", "avatar-default-symbolic"),
    ("Series", "media-playlist-consecutive-symbolic"),
    ("Tags", "folder-saved-search-symbolic"),
    ("On Reader", "media-removable-symbolic"),
    ("History", "document-open-recent-symbolic"),
]


def _cmp(a: object, b: object) -> int:
    return (a > b) - (a < b)  # type: ignore[operator]


BuildWidget = Callable[[BookObject], Gtk.Widget]  # create_book_card or create_book_row


def _fill(holder: Gtk.Box, book_obj: BookObject, build: BuildWidget) -> None:
    while (child := holder.get_first_child()) is not None:
        holder.remove(child)
    holder.append(build(book_obj))


@dataclass
class _ImportTally:
    total: int
    done: int = 0
    imported: int = 0
    added: int = 0
    failed: int = 0
    skipped: list[ImportResult] = field(default_factory=list)
    new_book_ids: set[str] = field(default_factory=set)  # a Calibre book's 2nd format isn't "added"
    originals_trashed: int = 0
    originals_kept: int = 0  # meant to be moved, but the Trash refused

    def summary(self, cancelled: bool, error: Exception | None) -> str:
        """One toast line, e.g. "Imported 2 book(s), skipped 'X' (already in library)"."""
        parts = []
        if self.imported:
            parts.append(f"imported {self.imported} book(s)")
        if self.added:
            parts.append(f"added {self.added} format(s) to existing books")
        if len(self.skipped) == 1:
            skip = self.skipped[0]
            if skip.status is ImportStatus.FORMAT_EXISTS:
                parts.append(
                    f"skipped '{skip.book.title}' (already in library as {skip.format.upper()})"
                )
            else:
                parts.append(f"skipped '{skip.book.title}' (already in library)")
        elif self.skipped:
            parts.append(f"skipped {len(self.skipped)} already in library")
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.originals_trashed:
            parts.append(f"moved {self.originals_trashed} original(s) to the Trash")
        if self.originals_kept:
            parts.append(f"{self.originals_kept} original(s) kept: could not move to the Trash")
        text = ", ".join(parts)

        if error is not None:
            prefix = f"Import stopped by an unexpected error ({error})"
        elif cancelled and self.done < self.total:
            prefix = f"Import cancelled after {self.done} of {self.total}"
        else:
            return text[:1].upper() + text[1:]
        return f"{prefix}: {text}" if text else prefix


class Window(Adw.ApplicationWindow):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(title="Glafka", default_width=1120, default_height=760, **kwargs)
        self.library: LibraryManager | None = None
        self.store = Gio.ListStore(item_type=BookObject)
        self.toast_overlay = Adw.ToastOverlay()
        self._search_text = ""
        self._sort = AppConfig.load().sort
        self._list_view = AppConfig.load().list_view
        if self._sort not in SORTS.values():
            self._sort = BookSort.TITLE_ASC
        self._category = "All Books"
        # Tile -> (bound book, its "changed" handler); disconnected on unbind as tiles recycle
        self._card_handlers: dict[Gtk.ListItem, tuple[BookObject, int]] = {}
        # (title, detail) shown instead of the grid while the library can't be used
        self._error: tuple[str, str] | None = None
        self._on_reader_ids: set[str] = set()  # on a USB reader at its last check
        self._scanned_mounts: set[str] = set()  # plugged-in destinations already scanned
        self._pending_sends: set[Job] = set()
        self._destinations: list[Destination] = []
        self._emails_in_flight: set[tuple[str, str]] = set()  # (book id, destination id)
        self.worker = LibraryWorker()
        self._import_job: Job | None = None
        self._closing = False
        self.connect("close-request", self._on_close_request)
        self._setup_ui()

    def _setup_ui(self) -> None:
        is_first_run = not AppConfig.exists()
        config = AppConfig.load()
        if is_first_run:
            try:
                config.save()
            except OSError as e:
                log.warning("Could not save config: %s", e)

        root_split = Adw.NavigationSplitView(min_sidebar_width=220, max_sidebar_width=260)
        root_split.set_sidebar(self._build_sidebar())

        # Content: header, then grid with the details pane overlaid on the right
        content_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self._search_entry = search_entry = Gtk.SearchEntry(
            placeholder_text="Search title, author, series...", width_request=280
        )
        header.set_title_widget(search_entry)
        add_menu = Gio.Menu()
        add_menu.append("Add Folder or Calibre Library…", "win.add-folder")
        add_btn = Adw.SplitButton(
            icon_name="list-add-symbolic",
            tooltip_text="Add Books",
            dropdown_tooltip="More Ways to Add",
            menu_model=add_menu,
        )
        header.pack_start(add_btn)
        menu = Gio.Menu()
        menu.append("Preferences", "win.preferences")
        menu.append("About Glafka", "win.about")
        header.pack_end(
            Gtk.MenuButton(
                icon_name="open-menu-symbolic",
                menu_model=menu,
                primary=True,
                tooltip_text="Main Menu",
            )
        )
        self._sort_button = Gtk.MenuButton(menu_model=self._build_sort_menu())
        header.pack_end(self._sort_button)
        self._view_button = Gtk.Button()
        self._view_button.connect("clicked", lambda _btn: self._set_list_view(not self._list_view))
        header.pack_end(self._view_button)
        content_view.add_top_bar(header)

        self._filter = Gtk.CustomFilter.new(self._filter_func)
        self._filter_model = Gtk.FilterListModel(model=self.store, filter=self._filter)
        self._sorter = Gtk.CustomSorter.new(self._sort_func)
        sort_model = Gtk.SortListModel(model=self._filter_model, sorter=self._sorter)
        # No autoselect: the details pane is open exactly while a book is selected
        selection = Gtk.SingleSelection(model=sort_model, autoselect=False, can_unselect=True)
        self._selection = selection

        self._overlay_split = Adw.OverlaySplitView(
            sidebar_position=Gtk.PackType.END,
            min_sidebar_width=340,
            max_sidebar_width=380,
            show_sidebar=False,
        )
        self._details = DetailsPane(
            on_save=self._on_save_details,
            on_close=lambda: self._overlay_split.set_show_sidebar(False),
            on_delete=self._confirm_delete,
            on_send=self._send,
            on_reload=self._confirm_reload,
            on_reader_action=self._on_reader_action,
        )
        self._overlay_split.set_sidebar(self._details.container)
        self.drive_monitor = DriveMonitor(on_change=self._on_destinations_changed)

        grid_view = Gtk.GridView(
            model=selection,
            factory=self._book_factory(create_book_card),
            min_columns=2,
            max_columns=8,
            margin_start=16,
            margin_end=16,
            margin_top=16,
            margin_bottom=16,
        )
        grid_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, child=grid_view, vexpand=True
        )
        # Same selection model as the grid: switching views keeps the selected book
        list_view = Gtk.ListView(
            model=selection, factory=self._book_factory(create_book_row), show_separators=True
        )
        list_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, child=list_view, vexpand=True
        )

        # Grid, empty state, or error state
        self._view_stack = Gtk.Stack()
        empty_page = Adw.StatusPage(
            icon_name="accessories-dictionary-symbolic",
            title="No Books in Library",
            description=(
                "Add EPUB or PDF files, a folder, or an existing Calibre library. "
                "You can also drop them here."
            ),
        )
        empty_add_btn = Gtk.Button(label="Add Books", css_classes=["suggested-action", "pill"])
        empty_folder_btn = Gtk.Button(label="Add Folder…", css_classes=["pill"])
        empty_buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        empty_buttons.append(empty_add_btn)
        empty_buttons.append(empty_folder_btn)
        empty_page.set_child(empty_buttons)
        self._error_page = Adw.StatusPage(icon_name="dialog-error-symbolic")
        choose_btn = Gtk.Button(
            label="Choose Library Folder…",
            halign=Gtk.Align.CENTER,
            css_classes=["suggested-action", "pill"],
        )
        choose_btn.connect(
            "clicked", lambda _btn: choose_library_folder(self, self._switch_library)
        )
        self._error_page.set_child(choose_btn)
        self._no_results_page = no_results_page = Adw.StatusPage(
            icon_name="edit-find-symbolic",
            title="No Matching Books",
            description="No books match the search or this category.",
        )
        self._group_view = GroupView(on_activate=self._show_details)
        self._history_view = HistoryView(on_activate=self._open_book)
        self._regroup_pending = False
        self._view_stack.add_named(empty_page, "empty")
        self._view_stack.add_named(grid_scroll, "grid")
        self._view_stack.add_named(list_scroll, "list")
        self._view_stack.add_named(self._group_view.widget, "groups")
        self._view_stack.add_named(no_results_page, "no-results")
        self._view_stack.add_named(self._history_view.widget, "history")
        self._view_stack.add_named(self._error_page, "error")
        self._add_buttons = (add_btn, empty_add_btn, empty_folder_btn)

        self._progress = JobProgress(on_cancel=self._cancel_import)
        self._view_stack.set_vexpand(True)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.append(self._progress.widget)
        main_box.append(self._view_stack)
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        main_box.add_controller(drop_target)
        self._overlay_split.set_content(main_box)
        content_view.set_content(self._overlay_split)
        root_split.set_content(Adw.NavigationPage(title="Books", child=content_view))
        self.toast_overlay.set_child(root_split)
        self.set_content(self.toast_overlay)

        # Signals
        selection.connect("notify::selected-item", self._on_selection_changed)
        self._overlay_split.connect("notify::show-sidebar", self._on_details_visibility_changed)
        search_entry.connect("search-changed", self._on_search_changed)
        self._sidebar_list.connect("row-selected", self._on_category_selected)
        if (first_row := self._sidebar_list.get_row_at_index(0)) is not None:
            self._sidebar_list.select_row(first_row)

        add_btn.connect("clicked", lambda _btn: self.open_import_dialog())
        empty_add_btn.connect("clicked", lambda _btn: self.open_import_dialog())
        empty_folder_btn.connect("clicked", lambda _btn: self.open_folder_dialog())

        for name, callback in (
            ("add-books", self.open_import_dialog),
            ("add-folder", self.open_folder_dialog),
            ("search", self._search_entry.grab_focus),
            ("save-details", self._save_details),
            ("preferences", self.open_preferences),
            ("about", self.open_about),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _param, cb=callback: cb())
            self.add_action(action)

        self._filter_model.connect("items-changed", lambda *_args: self._update_view())
        self._set_list_view(self._list_view, save=False)
        self.store.connect("items-changed", lambda *_args: self._schedule_regroup())
        self._open_library(config.library_path)

        if is_first_run:
            GLib.idle_add(self._first_run_prompt)

    def _build_sidebar(self) -> Adw.NavigationPage:
        sidebar_view = Adw.ToolbarView()
        sidebar_view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))
        self._sidebar_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["navigation-sidebar"]
        )
        for title, icon in CATEGORIES:
            row = Adw.ActionRow(title=title, activatable=True)
            row.add_prefix(Gtk.Image(icon_name=icon))
            self._sidebar_list.append(row)
        sidebar_view.set_content(
            Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self._sidebar_list)
        )
        return Adw.NavigationPage(title="Glafka", child=sidebar_view)

    # Model callbacks

    def _filter_func(self, item: BookObject, *_args: object) -> bool:
        book = item.book
        query = self._search_text.lower()
        if query and not (
            query in book.title.lower()
            or query in book.author.lower()
            or (book.series is not None and query in book.series.lower())
        ):
            return False
        if self._category == "On Reader":
            return book.id in self._on_reader_ids
        return True

    def _sort_func(self, a: BookObject, b: BookObject, *_args: object) -> int:
        x, y = a.book, b.book
        match self._category:
            case "Recently Added":
                return _cmp(y.created_at, x.created_at)
            case _:
                key, reverse = sort_key(self._sort)
                order = _cmp(key(x), key(y))
                return -order if reverse else order

    def _book_factory(self, build: BuildWidget) -> Gtk.SignalListItemFactory:
        """Tiles or rows for the flat views; each redraws itself when its book changes."""
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind, build)
        factory.connect("unbind", self._on_factory_unbind)
        return factory

    def _on_factory_setup(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        item.set_child(Gtk.Box(orientation=Gtk.Orientation.VERTICAL))

    def _on_factory_bind(
        self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem, build: BuildWidget
    ) -> None:
        book_obj = item.get_item()
        holder = item.get_child()
        _fill(holder, book_obj, build)
        handler_id = book_obj.connect("changed", lambda obj: _fill(holder, obj, build))
        self._card_handlers[item] = (book_obj, handler_id)

    def _on_factory_unbind(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        entry = self._card_handlers.pop(item, None)
        if entry is not None:
            book_obj, handler_id = entry
            book_obj.disconnect(handler_id)

    # UI events

    def _on_selection_changed(self, selection: Gtk.SingleSelection, _pspec: object) -> None:
        item = selection.get_selected_item()
        self._details.set_book(item)
        self._update_reader_status()
        self._overlay_split.set_show_sidebar(item is not None)

    def _on_details_visibility_changed(self, split: Adw.OverlaySplitView, _pspec: object) -> None:
        if not split.get_show_sidebar():
            # However the pane closed (button, click outside), unselect: the same tile can reopen it
            self._selection.set_selected(Gtk.INVALID_LIST_POSITION)

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._search_text = entry.get_text()
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._schedule_regroup()
        self._refresh_history()

    def _on_category_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if isinstance(row, Adw.ActionRow):
            self._category = row.get_title()
            self._update_sort_button()
            self._view_button.set_sensitive(self._category != "History")  # no grid/list there
            self._refresh_history()
            self._overlay_split.set_show_sidebar(False)  # the shown book may not be in this view
            self._filter.changed(Gtk.FilterChange.DIFFERENT)
            self._sorter.changed(Gtk.SorterChange.DIFFERENT)
            self._regroup()

    # Sort menu

    def _build_sort_menu(self) -> Gio.Menu:
        by, descending = next(k for k, v in SORTS.items() if v is self._sort)
        self._sort_by = Gio.SimpleAction.new_stateful(
            "sort-by", GLib.VariantType.new("s"), GLib.Variant.new_string(by)
        )
        self._sort_order = Gio.SimpleAction.new_stateful(
            "sort-order",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string("desc" if descending else "asc"),
        )
        for action in (self._sort_by, self._sort_order):
            action.connect("change-state", self._on_sort_changed)
            self.add_action(action)

        by_section = Gio.Menu()
        by_section.append("Title", "win.sort-by::title")
        by_section.append("Author", "win.sort-by::author")
        by_section.append("Date Added", "win.sort-by::date")
        order_section = Gio.Menu()
        order_section.append("Ascending", "win.sort-order::asc")
        order_section.append("Descending", "win.sort-order::desc")
        menu = Gio.Menu()
        menu.append_section("Sort By", by_section)
        menu.append_section(None, order_section)
        return menu

    def _on_sort_changed(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        by = self._sort_by.get_state().get_string()
        descending = self._sort_order.get_state().get_string() == "desc"
        self._sort = SORTS[(by, descending)]
        self._update_sort_button()
        self._sorter.changed(Gtk.SorterChange.DIFFERENT)
        self._schedule_regroup()
        config = AppConfig.load()
        config.sort = self._sort
        try:
            config.save()
        except OSError as e:
            log.warning("Could not save the sort order: %s", e)

    def _set_list_view(self, list_view: bool, save: bool = True) -> None:
        self._list_view = list_view
        self._group_view.set_list_mode(list_view)
        self._view_button.set_icon_name("view-grid-symbolic" if list_view else "view-list-symbolic")
        self._view_button.set_tooltip_text("Grid View" if list_view else "List View")
        self._update_view()
        if not save:
            return
        config = AppConfig.load()
        config.list_view = list_view
        try:
            config.save()
        except OSError as e:
            log.warning("Could not save the view mode: %s", e)

    def _update_sort_button(self) -> None:
        fixed = FIXED_ORDER.get(self._category)
        descending = self._sort_order.get_state().get_string() == "desc"
        self._sort_button.set_icon_name(
            "view-sort-descending-symbolic" if descending else "view-sort-ascending-symbolic"
        )
        self._sort_button.set_sensitive(fixed is None)
        self._sort_button.set_tooltip_text(fixed or "Sort")

    # Grouped categories

    def _schedule_regroup(self) -> None:
        """Coalesce bursts (an import adds books one by one) into one rebuild."""
        if self._category in GROUPED_CATEGORIES and not self._regroup_pending:
            self._regroup_pending = True
            GLib.idle_add(self._regroup)

    def _regroup(self) -> bool:
        self._regroup_pending = False
        group_by = GROUPED_CATEGORIES.get(self._category)
        if group_by is not None:
            objects = [self.store.get_item(i) for i in range(self.store.get_n_items())]
            visible = {o.book.id: o for o in objects if self._filter_func(o)}
            groups = group_books([o.book for o in visible.values()], group_by, self._sort)
            self._group_view.set_groups(
                [(g.name, [visible[b.id] for b in g.books]) for g in groups]
            )
        self._update_view()
        return GLib.SOURCE_REMOVE

    def _show_details(self, book_obj: BookObject) -> None:
        self._details.set_book(book_obj)
        self._update_reader_status()
        self._overlay_split.set_show_sidebar(True)

    def _on_save_details(
        self,
        book_id: str,
        title: str,
        author: str,
        series: str | None,
        series_index: int | None,
        description: str | None,
        tags: list[str],
    ) -> None:
        if self.library is None:
            return
        try:
            self.library.update_metadata(
                book_id, title, author, series, series_index, description, tags
            )
        except RECOVERABLE_ERRORS as e:
            log.error("Failed to update metadata: %s", e)
            self.toast(f"Failed to save: {e}")
            return

        updated = self.library.get_book_by_id(book_id)
        if updated is not None:
            self._refresh_book(updated)
            self._details.refresh_cover(updated)
        self.toast(f"Saved changes for '{title}'")
        self._sync_files(book_id)

    def _sync_files(self, book_id: str) -> None:
        """Write the saved metadata into the book's files and rename its folder, on the worker.

        May run seconds later (behind an import); only problems are reported.
        """
        if self.library is None:
            return
        job = self.worker.submit(
            self.library.root_dir,
            lambda library: [library.sync_files(book_id)],
            on_item=lambda sync: self._on_files_synced(job, sync),
            on_done=lambda error: self._on_sync_failed(job, error) if error else None,
        )

    def _on_files_synced(self, job: Job, sync: FileSync) -> None:
        if not self._is_current(job):
            return
        if sync.book is not None:
            self._refresh_book(sync.book)  # new paths after a rename, new size after a rewrite
        if sync.problems:
            title = sync.book.title if sync.book else "the book"
            self.toast(f"Saved '{title}', but: {'; '.join(sync.problems)}")

    def _on_sync_failed(self, job: Job, error: Exception) -> None:
        if self._is_current(job):
            self.toast(f"Saved, but updating the book's files failed: {error}")

    def _confirm_reload(self, book_id: str) -> None:
        position = self._store_position(book_id)
        if position is None or self.library is None:
            return
        book = self.store.get_item(position).book
        source = next((f for fmt in ("epub", "pdf") for f in book.files if f.format == fmt), None)
        if source is None:
            self.toast("Glafka can only read details from EPUB and PDF files")
            return
        dialog = Adw.AlertDialog(
            heading=f"Reload “{book.title}” from Its File?",
            body=(
                f"What the {source.format.upper()} file says replaces the title, author, series, "
                "description, tags and cover shown here. Details the file doesn't have stay. "
                "Unsaved changes in the details pane are lost."
            ),
            default_response="cancel",
            close_response="cancel",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reload", "Reload")
        dialog.set_response_appearance("reload", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, response: self._reload_book(book_id) if response == "reload" else None,
        )
        dialog.present(self)

    def _reload_book(self, book_id: str) -> None:
        """Read the book's details from its file, on the worker (PDF covers take a moment)."""
        if self.library is None:
            return
        job = self.worker.submit(
            self.library.root_dir,
            lambda library: [library.reload_metadata(book_id)],
            on_item=lambda sync: self._on_reloaded(job, sync),
            on_done=lambda error: self._on_reload_failed(job, error) if error else None,
        )

    def _on_reloaded(self, job: Job, sync: FileSync) -> None:
        if not self._is_current(job) or sync.book is None:
            return
        self._refresh_book(sync.book)
        shown = self._details.current_book
        if shown is not None and shown.book.id == sync.book.id:
            shown.update(sync.book)  # grouped views may show another object for the same book
            self._details.set_book(shown)  # the pane doesn't follow "changed" by itself
            self._update_reader_status()
        text = f"Reloaded '{sync.book.title}' from its file"
        self.toast(f"{text}, but: {'; '.join(sync.problems)}" if sync.problems else text)

    def _on_reload_failed(self, job: Job, error: Exception) -> None:
        if self._is_current(job):
            self.toast(f"Could not reload the details: {error}")

    def _store_position(self, book_id: str) -> int | None:
        for i in range(self.store.get_n_items()):
            if self.store.get_item(i).book.id == book_id:
                return i
        return None

    def _confirm_delete(self, book_id: str) -> None:
        position = self._store_position(book_id)
        if position is None:
            return
        book = self.store.get_item(position).book
        formats = ", ".join(f.format.upper() for f in book.files) or "no files"
        dialog = Adw.AlertDialog(
            heading=f"Delete “{book.title}”?",
            body=(
                f"Its files ({formats}) and cover are removed from the library folder. "
                "The original files you imported from are not affected."
            ),
            default_response="cancel",
            close_response="cancel",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda _d, response: self._delete_book(book_id) if response == "delete" else None,
        )
        dialog.present(self)

    def _delete_book(self, book_id: str) -> None:
        if self.library is None:
            return
        position = self._store_position(book_id)
        title = self.store.get_item(position).book.title if position is not None else book_id
        try:
            leftovers = self.library.delete_book(book_id)
        except RECOVERABLE_ERRORS as e:
            log.error("Failed to delete %s: %s", book_id, e)
            self.toast(f"Could not delete '{title}': {e}")
            return
        shown = self._details.current_book
        if shown is not None and shown.book.id == book_id:
            self._overlay_split.set_show_sidebar(False)  # grouped views have no selection to drop
        if position is not None:
            self.store.remove(position)
        self._refresh_history()  # its entries stay, without a link to the book
        if leftovers:
            names = ", ".join(p.name for p in leftovers)
            self.toast(f"Deleted '{title}', but could not remove: {names}")
        else:
            self.toast(f"Deleted '{title}'")

    def _refresh_book(self, book: Book) -> None:
        """Show new data for a book already in the grid (redraws its tile, re-sorts)."""
        for i in range(self.store.get_n_items()):
            book_obj = self.store.get_item(i)
            if book_obj.book.id == book.id:
                book_obj.update(book)
                # Same object: no redraw, but filter/sort re-evaluate the item
                self.store.splice(i, 1, [book_obj])
                return

    # Actions

    def toast(self, text: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=text, use_markup=False))

    def reload_books(self) -> None:
        if self.library is None:
            return
        try:
            books = self.library.get_books(BookFilter(), BookSort.TITLE_ASC)
        except RECOVERABLE_ERRORS as e:
            log.error("Failed to load books: %s", e)
            self._error = ("Could Not Load Books", f"{self.library.root_dir}\n\n{e}")
            books = []
        else:
            self._error = None
        try:
            self._on_reader_ids = self.library.books_on_readers()
        except RECOVERABLE_ERRORS as e:
            log.warning("Could not load which books are on readers: %s", e)
        on_reader = self._on_reader_ids
        self.store.splice(
            0, self.store.get_n_items(), [BookObject(b, b.id in on_reader) for b in books]
        )
        self._update_view()

    def _update_view(self) -> None:
        """Error page, empty state or grid, from the current state."""
        if self._error is not None:
            title, detail = self._error
            self._error_page.set_title(title)
            self._error_page.set_description(GLib.markup_escape_text(detail))
            page = "error"
        elif self._category == "History":
            page = "history"  # history outlives books: shown even with an empty library
        elif self.store.get_n_items() == 0:
            page = "empty"
        elif self._category in GROUPED_CATEGORIES:
            page = "groups" if self._group_view.has_groups() else "no-results"
        else:
            flat = "list" if self._list_view else "grid"
            page = flat if self._filter_model.get_n_items() else "no-results"
        if page == "no-results":
            self._describe_no_results()
        self._view_stack.set_visible_child_name(page)
        for button in self._add_buttons:
            button.set_sensitive(self.library is not None)

    def _describe_no_results(self) -> None:
        page = self._no_results_page
        if self._category == "On Reader" and not self._search_entry.get_text().strip():
            has_usb = any(d.kind == "usb" for d in self._destinations)
            page.set_icon_name("media-removable-symbolic")
            page.set_title("No Books on Your Reader")
            page.set_description(
                "Plug in your reader to check which of your books are on it."
                if has_usb
                else "Add your reader in Preferences, Sending, then plug it in."
            )
        else:
            page.set_icon_name("edit-find-symbolic")
            page.set_title("No Matching Books")
            page.set_description("No books match the search or this category.")

    def _save_details(self) -> None:
        if self._overlay_split.get_show_sidebar():
            self._details.save()

    def open_import_dialog(self) -> None:
        ebook_filter = Gtk.FileFilter(name="Ebook Files (*.epub, *.pdf, *.mobi)")
        for pattern in ("*.epub", "*.pdf", "*.mobi", "*.azw3"):
            ebook_filter.add_pattern(pattern)
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(ebook_filter)
        dialog = Gtk.FileDialog(title="Select Ebooks", filters=filters)
        dialog.open_multiple(self, None, self._on_import_files_chosen)

    def _on_import_files_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return  # cancelled
        paths = [Path(p) for i in range(files.get_n_items()) if (p := files.get_item(i).get_path())]
        self.import_paths(paths)

    def open_folder_dialog(self) -> None:
        dialog = Gtk.FileDialog(title="Select Folders or a Calibre Library")
        dialog.select_multiple_folders(self, None, self._on_folders_chosen)

    def _on_folders_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            folders = dialog.select_multiple_folders_finish(result)
        except GLib.Error:
            return  # cancelled
        paths = [
            Path(p) for i in range(folders.get_n_items()) if (p := folders.get_item(i).get_path())
        ]
        self.import_paths(paths)

    def _on_drop(self, _target: Gtk.DropTarget, files: Gdk.FileList, _x: float, _y: float) -> bool:
        paths = [Path(p) for f in files.get_files() if (p := f.get_path())]
        if self.library is None or not paths:
            return False
        self.import_paths(paths)
        return True

    def import_paths(self, paths: list[Path]) -> None:
        """Import files and folders on the worker: first find the ebooks, then import them."""
        if self.library is None or not paths:
            return
        if self._import_job is not None:
            self.toast("Wait for the current import to finish")
            return
        plans: list[ImportPlan] = []
        job = self.worker.submit(
            self.library.root_dir,
            lambda library: [library.plan_import(paths)],
            on_item=plans.append,
            on_done=lambda error: self._on_scan_done(job, plans, error),
        )
        self._import_job = job
        self._progress.show_message("Looking for ebooks…")

    def _on_scan_done(self, job: Job, plans: list[ImportPlan], error: Exception | None) -> None:
        if self._import_job is job:
            self._import_job = None
        if self._closing:
            return
        if not self._is_current(job) or job.cancelled or error is not None or not plans:
            self._progress.hide()
            if error is not None:
                self.toast(f"Could not look for ebooks: {error}")
            return
        plan = plans[0]
        if not plan.items:
            self._progress.hide()
            self.toast("No ebooks found")
            return
        self._start_import(plan)

    def _start_import(self, plan: ImportPlan) -> None:
        """Tiles appear as each item finishes, then one summary toast."""
        assert self.library is not None
        items = plan.items
        tally = _ImportTally(total=len(items))
        remove_original = move_to_trash if AppConfig.load().move_imported_files else None
        job = self.worker.submit(
            self.library.root_dir,
            lambda library: library.run_import(plan, remove_original),
            on_item=lambda outcome: self._on_import_item(job, items, tally, outcome),
            on_done=lambda error: self._on_import_done(job, tally, error),
        )
        self._import_job = job
        self._progress.show(0, len(items), items[0].name)

    def _on_import_item(
        self, job: Job, items: list[Path], tally: _ImportTally, outcome: ItemOutcome
    ) -> None:
        if not self._is_current(job):
            return  # window closing, or another library opened since the job started
        tally.done += 1
        if outcome.error is not None:
            tally.failed += 1
        if outcome.original_removed:
            tally.originals_trashed += 1
        elif outcome.removal_error is not None:
            tally.originals_kept += 1
        for result in outcome.results:
            match result.status:
                case ImportStatus.IMPORTED:
                    self.store.append(BookObject(result.book))
                    tally.imported += 1
                    tally.new_book_ids.add(result.book.id)
                case ImportStatus.FORMAT_ADDED:
                    self._refresh_book(result.book)  # same book: update its tile, don't add one
                    if result.book.id not in tally.new_book_ids:
                        tally.added += 1
                case ImportStatus.ALREADY_IN_LIBRARY | ImportStatus.FORMAT_EXISTS:
                    tally.skipped.append(result)
        if tally.done < tally.total and not job.cancelled:
            self._progress.show(tally.done, tally.total, items[tally.done].name)

    def _on_import_done(self, job: Job, tally: _ImportTally, error: Exception | None) -> None:
        if self._import_job is job:
            self._import_job = None
        if self._closing:
            return
        self._progress.hide()
        if self._is_current(job) and (summary := tally.summary(job.cancelled, error)):
            self.toast(summary)

    def _is_current(self, job: Job) -> bool:
        return not self._closing and self.library is not None and self.library.root_dir == job.root

    def _cancel_import(self) -> None:
        if self._import_job is not None:
            self._import_job.cancel()

    def _on_close_request(self, _window: Adw.ApplicationWindow) -> bool:
        self._closing = True
        self.worker.shutdown()  # current file finishes; nothing after it
        thumbnails().shutdown()
        return False  # let the window close

    def open_preferences(self) -> None:
        destinations = None
        if self.library is not None:
            destinations = DestinationSettings(
                get=self.library.get_destinations,
                add=self._add_destination,
                update=self._update_destination,
                remove=self._remove_destination,
                drives=self.drive_monitor.removable_drives,
            )
        show_preferences_dialog(self, self._switch_library, destinations)

    # Destinations

    def _load_destinations(self) -> None:
        destinations: list[Destination] = []
        if self.library is not None:
            try:
                destinations = self.library.get_destinations()
            except RECOVERABLE_ERRORS as e:
                log.warning("Could not load destinations: %s", e)
        self._destinations = destinations
        self.drive_monitor.set_destinations(destinations)

    def _add_destination(self, destination: Destination) -> None:
        if self.library is not None:
            self.library.add_destination(destination)
            self._load_destinations()

    def _update_destination(self, destination: Destination) -> None:
        if self.library is not None:
            self.library.update_destination(destination)
            self._load_destinations()  # buttons in the details pane show the new name

    def _remove_destination(self, destination_id: str) -> None:
        if self.library is not None:
            self.library.delete_destination(destination_id)
            self._load_destinations()

    def _on_destinations_changed(self) -> None:
        """Drives come and go (signals arrive in bursts); scan each reader once per plug-in."""
        connected = self.drive_monitor.connected()
        email = [r for r in self._destinations if r.kind == "email"]
        self._details.set_send_targets([c.destination for c in connected] + email)
        for drive in connected:
            if drive.destination.id not in self._scanned_mounts:
                self._scan(drive.destination, drive.root)
        self._scanned_mounts = {c.destination.id for c in connected}
        self._update_reader_status()

    # What is on the readers

    def _scan(self, destination: Destination, root: Path) -> None:
        if self.library is None:
            return
        job = self.worker.submit(
            self.library.root_dir,
            lambda library: [library.scan_device(destination, root)],
            on_item=lambda result: self._on_scanned(job, destination, result),
            on_done=lambda error: self._on_reader_job_done(job, destination, error),
        )
        self._pending_sends.add(job)  # Eject waits for it

    def _on_scanned(self, job: Job, destination: Destination, result: ScanResult) -> None:
        if not self._is_current(job):
            return
        self._load_destinations()  # its "last checked" changed
        self._refresh_on_reader()
        count = len(result.book_ids)
        found = f"{count} of your books are" if count != 1 else "1 of your books is"
        self.toast(
            f"Checked {destination.name}: {found if count else 'none of your books are'} on it"
        )

    def _on_reader_job_done(
        self, job: Job, destination: Destination, error: Exception | None
    ) -> None:
        self._pending_sends.discard(job)
        if error is not None and self._is_current(job):
            self.toast(f"Could not read {destination.name}: {error}")

    def _refresh_on_reader(self) -> None:
        """Badges, the On Reader list and the details pane, from the index."""
        if self.library is None:
            return
        try:
            self._on_reader_ids = self.library.books_on_readers()
        except RECOVERABLE_ERRORS as e:
            log.warning("Could not load which books are on readers: %s", e)
            return
        for i in range(self.store.get_n_items()):
            book_obj = self.store.get_item(i)
            on_reader = book_obj.book.id in self._on_reader_ids
            if book_obj.on_reader != on_reader:
                book_obj.on_reader = on_reader
                book_obj.emit("changed")  # flat views redraw the tile
        if self._category == "On Reader":
            self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._schedule_regroup()
        self._update_reader_status()
        self._refresh_history()

    def _refresh_history(self) -> None:
        """Rebuild the History page, only while it is showing."""
        if self._category != "History" or self.library is None:
            return
        try:
            entries = self.library.history()
        except RECOVERABLE_ERRORS as e:
            log.warning("Could not load the history: %s", e)
            return
        books = {
            o.book.id: o.book
            for o in (self.store.get_item(i) for i in range(self.store.get_n_items()))
        }
        self._history_view.set_entries(entries, books, self._destinations, self._search_text)

    def _open_book(self, book_id: str) -> None:
        position = self._store_position(book_id)
        if position is not None:
            self._show_details(self.store.get_item(position))

    def _update_reader_status(self) -> None:
        shown = self._details.current_book
        if shown is None or self.library is None:
            self._details.set_reader_status([])
            return
        book_id = shown.book.id
        try:
            copies = self.library.device_copies(book_id)
            history = self.library.history(book_id)
        except RECOVERABLE_ERRORS as e:
            log.warning("Could not load where '%s' is: %s", shown.book.title, e)
            return
        connected = {c.destination.id for c in self.drive_monitor.connected()}
        rows = []
        for dest in self._destinations:
            if dest.kind == "usb":
                rows.append(_usb_status(dest, copies, dest.id in connected))
                continue
            sent = next(
                (
                    e
                    for e in history
                    if e.destination_id == dest.id and e.action == HistoryAction.SENT
                ),
                None,
            )
            if sent is not None:
                seen = "" if copies else ", not seen on a reader yet"
                rows.append(ReaderStatus(dest.id, f"Emailed to {dest.name}", _when(sent.at) + seen))
        self._details.set_reader_status(rows)
        self._details.set_history(
            [(describe(e)[0], details(e, with_date=True), describe(e)[1]) for e in history]
        )

    def _on_reader_action(self, book_id: str, destination_id: str, action: str) -> None:
        dest = next((d for d in self._destinations if d.id == destination_id), None)
        if self.library is None or dest is None:
            return
        if action == "forget":
            try:
                self.library.forget_device_copies(dest, book_id)
            except RECOVERABLE_ERRORS as e:
                self.toast(f"Could not change it: {e}")
                return
            self._refresh_on_reader()
            self.toast(f"Marked as removed from {dest.name}")
        elif action == "delete":
            self._confirm_remove_from_reader(book_id, dest)

    def _confirm_remove_from_reader(self, book_id: str, dest: Destination) -> None:
        position = self._store_position(book_id)
        if position is None or self.library is None:
            return
        book = self.store.get_item(position).book
        paths = [c.path for c in self.library.device_copies(book_id) if c.destination_id == dest.id]
        inside = [p for p in paths if _in_books_folder(p, dest)]
        outside = [p for p in paths if not _in_books_folder(p, dest)]
        body = (
            f"Removes {', '.join(inside)} from {dest.name}. The book stays in your library. "
            "The reader may forget your reading position."
        )
        if outside:
            body += f" The copy in {', '.join(outside)} stays; delete it on the reader."
        dialog = Adw.AlertDialog(
            heading=f"Delete “{book.title}” from {dest.name}?",
            body=body,
            default_response="cancel",
            close_response="cancel",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda _d, r: self._remove_from_reader(book, dest) if r == "delete" else None,
        )
        dialog.present(self)

    def _remove_from_reader(self, book: Book, dest: Destination) -> None:
        target = self.drive_monitor.find(dest.id)  # now: it may have been unplugged
        if self.library is None or target is None:
            self.toast(f"{dest.name} is not connected any more")
            return
        root = target.root
        job = self.worker.submit(
            self.library.root_dir,
            lambda library: [library.remove_from_device(book.id, dest, root)],
            on_item=lambda result: self._on_removed_from_reader(job, book, dest, result),
            on_done=lambda error: self._on_reader_job_done(job, dest, error),
        )
        self._pending_sends.add(job)

    def _on_removed_from_reader(
        self, job: Job, book: Book, dest: Destination, result: RemoveResult
    ) -> None:
        if not self._is_current(job):
            return
        self._refresh_on_reader()
        text = f"Deleted '{book.title}' from {dest.name}"
        if result.failed:
            text = f"Could not delete {', '.join(result.failed)} from {dest.name}"
        elif result.kept:
            text += f"; the copy in {', '.join(result.kept)} is still there"
        self.toast(text)

    def _send(self, book_id: str, destination_id: str, replace: bool = False) -> None:
        """Copy the book to a plugged-in reader, or email it, on the worker."""
        destination = next((r for r in self._destinations if r.id == destination_id), None)
        if destination is not None and destination.kind == "email":
            self._send_email(book_id, destination)
            return
        target = self.drive_monitor.find(destination_id)  # now: it may have been unplugged
        if self.library is None or target is None:
            self.toast("That reader is not connected any more")
            return
        destination, root = target.destination, target.root
        job = self.worker.submit(
            self.library.root_dir,
            lambda library: [library.send_to_usb(book_id, destination, root, replace)],
            on_item=lambda result: self._on_sent(job, destination, result),
            on_done=lambda error: self._on_send_failed(job, destination, error) if error else None,
        )
        self._pending_sends.add(job)

    def _send_email(self, book_id: str, destination: Destination) -> None:
        account = AppConfig.load().email_account
        if account is None:
            self._action_toast(
                "Set up a sending account to email books", "Preferences", self.open_preferences
            )
            return
        key = (book_id, destination.id)
        if key in self._emails_in_flight:
            self.toast(f"Already sending it to {destination.name}")  # each email is another copy
            return
        self._emails_in_flight.add(key)
        self.toast(f"Sending to {destination.name}…")

        def on_password(password: str | None, error: str | None) -> None:
            if self.library is None or self._closing or not password:
                self._emails_in_flight.discard(key)
                if error is not None:
                    self.toast(f"Could not read the keyring: {error}")
                elif not password and self.library is not None:
                    self._action_toast(
                        f"No app password for {account.address} (not saved, or keyring locked)",
                        "Preferences",
                        self.open_preferences,
                    )
                return

            def done(error: Exception | None) -> None:
                self._emails_in_flight.discard(key)
                if error is not None:
                    self._on_send_failed(job, destination, error)

            job = self.worker.submit(
                self.library.root_dir,
                lambda library: [library.send_by_email(book_id, destination, account, password)],
                on_item=lambda result: self._on_sent(job, destination, result),
                on_done=done,
            )

        lookup_password(account, on_password)

    def _on_sent(self, job: Job, destination: Destination, result: SendResult) -> None:
        self._pending_sends.discard(job)
        if not self._is_current(job):
            return
        title = result.book.title
        match result.status:
            case SendStatus.SENT | SendStatus.ALREADY_THERE:
                if destination.kind == "email":
                    self._update_reader_status()
                    self._refresh_history()
                    self.toast(f"Emailed '{title}' to {destination.name}")
                    return
                self._refresh_on_reader()
                text = (
                    f"Copied '{title}' to {destination.name}"
                    if result.status is SendStatus.SENT
                    else f"'{title}' is already on {destination.name}"
                )
                self._action_toast(text, "Eject", lambda: self._eject(destination))
            case SendStatus.DIFFERENT_COPY_THERE:
                self._action_toast(
                    f"{destination.name} already has a different copy of '{title}'",
                    "Replace",
                    lambda: self._send(result.book.id, destination.id, replace=True),
                )
            case SendStatus.NO_SUITABLE_FORMAT:
                has = ", ".join(f.format.upper() for f in result.book.files) or "no files"
                takes = ", ".join(f.upper() for f in destination.formats)
                self.toast(
                    f"{destination.name} takes {takes}; '{title}' is only available as {has}"
                )

    def _on_send_failed(self, job: Job, destination: Destination, error: Exception) -> None:
        self._pending_sends.discard(job)
        if self._is_current(job):
            self.toast(f"Could not send to {destination.name}: {error}")

    def _eject(self, destination: Destination) -> None:
        if self._pending_sends:
            self.toast("Wait until Glafka has finished with the reader")
            return
        target = self.drive_monitor.find(destination.id)
        if target is None:
            self.toast(f"{destination.name} can be unplugged")
            return
        mount, operation = target.mount, Gtk.MountOperation(parent=self)

        def done(mount: Gio.Mount, result: Gio.AsyncResult) -> None:
            try:
                if mount.can_eject():
                    mount.eject_with_operation_finish(result)
                else:
                    mount.unmount_with_operation_finish(result)
            except GLib.Error as e:
                self.toast(f"Could not eject {destination.name}: {e.message}")
                return
            self.toast(f"{destination.name} can be unplugged")

        if mount.can_eject():
            mount.eject_with_operation(Gio.MountUnmountFlags.NONE, operation, None, done)
        else:
            mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, operation, None, done)

    def _action_toast(self, text: str, label: str, action: Callable[[], None]) -> None:
        """Stays until dismissed: an offer like Replace must not vanish before it is read."""
        toast = Adw.Toast(
            title=text,
            use_markup=False,
            button_label=label,
            timeout=0,
            priority=Adw.ToastPriority.HIGH,
        )
        toast.connect("button-clicked", lambda _t: action())
        self.toast_overlay.add_toast(toast)

    def _first_run_prompt(self) -> bool:
        self.open_preferences()
        return GLib.SOURCE_REMOVE

    def _open_library(self, path: Path) -> bool:
        """Make the library at `path` current. On failure the current one, if any, stays."""
        try:
            library = LibraryManager.open(path)
        except RECOVERABLE_ERRORS as e:
            log.error("Failed to open library at %s: %s", path, e)
            if self.library is None:
                self._error = ("Could Not Open Library", f"{path}\n\n{e}")
                self._update_view()
            else:
                self.toast(f"Could not open library at {path}: {e}")
            return False
        self._cancel_import()  # its results belong to the old library
        if self.library is not None:
            self.library.close()
        self.library = library
        self._scanned_mounts = set()  # the new library's readers get scanned too
        self.reload_books()
        self._load_destinations()
        self._refresh_history()
        if self._error is None:
            books = (self.store.get_item(i).book for i in range(self.store.get_n_items()))
            thumbnails().prune([Path(b.cover_path) for b in books if b.cover_path])
        return True

    def _switch_library(self, new_path: Path) -> bool:
        if not self._open_library(new_path):
            return False
        # Saved only after a successful open, so a bad choice can't break the next start
        config = AppConfig.load()
        config.library_path = new_path
        try:
            config.save()
        except OSError as e:
            self.toast(f"Library switched, but the setting could not be saved: {e}")
            return True
        self.toast(f"Library switched to {new_path}")
        return True

    def open_about(self) -> None:
        Adw.AboutDialog(
            application_name="Glafka",
            application_icon=APP_ID,
            developer_name="Evripidis Vakalis",
            version=__version__,
            comments="A lightweight, GNOME-native ebook library manager",
            website="https://github.com/evripidisvak/glafka",
            issue_url="https://github.com/evripidisvak/glafka/issues",
            license_type=Gtk.License.GPL_3_0,
        ).present(self)


def _when(moment: datetime) -> str:
    return moment.astimezone().strftime("%-d %b %H:%M")


def _in_books_folder(path: str, dest: Destination) -> bool:
    folder = dest.books_folder.strip("/")
    return bool(folder) and path.startswith(folder + "/")


def _usb_status(dest: Destination, copies: list[DeviceCopy], connected: bool) -> ReaderStatus:
    when = _when(dest.last_checked) if dest.last_checked else None
    mine = [c for c in copies if c.destination_id == dest.id]
    if not mine:
        return ReaderStatus(
            dest.id, f"Not on {dest.name}", f"Checked {when}" if when else "Not checked yet"
        )
    checked = f"checked {when}" if when else "not checked yet"
    folders = ", ".join(sorted({c.path.split("/")[0] + "/" for c in mine}))
    action = None
    if connected and any(_in_books_folder(c.path, dest) for c in mine):
        action = ("delete", "Delete")
    elif not connected:
        action = ("forget", "Mark as Removed")
    return ReaderStatus(dest.id, f"On {dest.name}", f"In {folders}, {checked}", action)
