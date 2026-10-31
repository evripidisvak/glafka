from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from glafka.ui.book_card import create_book_card, create_book_row
from glafka.ui.book_object import BookObject

TILE_WIDTH = 156  # book card: 140 plus its margins


class GroupObject(GObject.Object):
    """One section: a group name and its books, in display order."""

    __gtype_name__ = "GlafkaGroupObject"

    def __init__(self, name: str, books: list[BookObject]) -> None:
        super().__init__()
        self.name = name
        self.books = books


class GroupView:
    """Books in sections, a header per group (Gtk.GridView can't show section headers).

    A ListView of sections, so only sections on screen are built; each wraps its tiles.
    """

    def __init__(self, on_activate: Callable[[BookObject], None]) -> None:
        self._on_activate = on_activate
        self._groups = Gio.ListStore(item_type=GroupObject)
        self._list_mode = False  # rows instead of tiles

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        self._list = Gtk.ListView(
            model=Gtk.NoSelection(model=self._groups), factory=factory, margin_bottom=16
        )
        self.widget = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, child=self._list, vexpand=True
        )

    def set_groups(self, groups: list[tuple[str, list[BookObject]]]) -> None:
        adjustment = self.widget.get_vadjustment()
        position = adjustment.get_value()
        self._groups.splice(
            0, self._groups.get_n_items(), [GroupObject(name, books) for name, books in groups]
        )

        def restore_scroll() -> bool:  # replacing every section would jump to the top
            adjustment.set_value(position)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(restore_scroll)

    def set_list_mode(self, list_mode: bool) -> None:
        if list_mode != self._list_mode:
            self._list_mode = list_mode
            # Fresh objects: GTK only rebinds a row whose item object changed, so
            # items_changed() on the same objects would leave every section as it was
            current = [self._groups.get_item(i) for i in range(self._groups.get_n_items())]
            self.set_groups([(g.name, g.books) for g in current])

    def has_groups(self) -> bool:
        return self._groups.get_n_items() > 0

    def _on_setup(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        item.set_activatable(False)
        item.set_selectable(False)
        item.set_focusable(False)
        title = Gtk.Label(xalign=0, css_classes=["title-4"])
        count = Gtk.Label(xalign=0, css_classes=["dim-label"])
        header = Gtk.Box(spacing=8, margin_start=8)
        header.append(title)
        header.append(count)
        tiles = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            halign=Gtk.Align.START,
            homogeneous=True,
            min_children_per_line=2,
            max_children_per_line=12,
            activate_on_single_click=True,
        )
        tiles.connect("child-activated", lambda _box, child: self._on_activate(child.book_obj))
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        rows.connect("row-activated", lambda _box, row: self._on_activate(row.book_obj))
        section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_start=16,
            margin_end=16,
            margin_top=16,
        )
        section.append(header)
        section.append(tiles)
        section.append(rows)
        item.set_child(section)
        item.parts = (title, count, tiles, rows)  # widgets the bind step fills

    def _on_bind(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        group: GroupObject = item.get_item()
        title, count, tiles, rows = item.parts
        title.set_text(group.name)
        n = len(group.books)
        count.set_text(f"{n} book" if n == 1 else f"{n} books")
        tiles.remove_all()
        rows.remove_all()
        tiles.set_visible(not self._list_mode)
        rows.set_visible(self._list_mode)
        if self._list_mode:
            for book_obj in group.books:
                row = Gtk.ListBoxRow(child=create_book_row(book_obj))
                row.book_obj = book_obj
                rows.append(row)
            return
        for book_obj in group.books:
            # A cover's natural width is its image size; the grid caps tiles by column, we clamp
            tile = Adw.Clamp(
                maximum_size=TILE_WIDTH,
                tightening_threshold=TILE_WIDTH,
                child=create_book_card(book_obj),
            )
            child = Gtk.FlowBoxChild(child=tile)
            child.book_obj = book_obj
            tiles.append(child)
