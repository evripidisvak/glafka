from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Adw, Gtk

from glafka.core.models import Destination
from glafka.core.text import html_to_text
from glafka.ui.book_card import generated_cover, load_texture
from glafka.ui.book_object import BookObject

# (book_id, title, author, series, series_index, description, tags)
SaveCallback = Callable[[str, str, str, str | None, int | None, str | None, list[str]], None]


@dataclass
class ReaderStatus:
    """One row of "On Your Readers"; the window decides the words and the action."""

    destination_id: str
    title: str
    detail: str
    action: tuple[str, str] | None = None  # ("delete" | "forget", button label)


class DetailsPane:
    """Side panel showing and editing one book's metadata."""

    def __init__(
        self,
        on_save: SaveCallback,
        on_close: Callable[[], None],
        on_delete: Callable[[str], None],
        on_send: Callable[[str, str], None],  # (book id, destination id)
        on_reload: Callable[[str], None],
        on_reader_action: Callable[[str, str, str], None],  # (book id, destination id, action)
    ) -> None:
        self._on_save = on_save
        self._on_send = on_send
        self._on_reader_action = on_reader_action
        self.current_book: BookObject | None = None
        self._shown_description = ""  # as displayed: plain text even if stored as HTML

        self.container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            width_request=340,
            css_classes=["view", "background"],
        )

        # Header: Save on the left, Close on the right; no window buttons in this sub-header
        header = Adw.HeaderBar(
            show_title=True,
            show_start_title_buttons=False,
            show_end_title_buttons=False,
            title_widget=Adw.WindowTitle(title="Book Details", subtitle=""),
        )
        save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"])
        save_btn.connect("clicked", self._save)
        header.pack_start(save_btn)
        close_btn = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close Details")
        close_btn.connect("clicked", lambda _btn: on_close())
        header.pack_end(close_btn)
        self.container.append(header)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_start=16,
            margin_end=16,
            margin_top=16,
            margin_bottom=24,
        )

        self.cover_slot = Gtk.Box(halign=Gtk.Align.CENTER)  # real cover or a generated one
        content.append(self.cover_slot)

        # One button per destination available now; see set_send_targets
        self._send_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER
        )
        content.append(self._send_box)

        # Where the book is: one row per reader; see set_reader_status
        self._status_group = Adw.PreferencesGroup(title="On Your Readers", visible=False)
        self._status_rows: list[Adw.ActionRow] = []
        content.append(self._status_group)

        meta_group = Adw.PreferencesGroup(title="Metadata")
        self.title_entry = Adw.EntryRow(title="Title")
        self.author_entry = Adw.EntryRow(title="Author")
        self.series_entry = Adw.EntryRow(title="Series")
        self.series_index_spin = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(
                value=1, lower=0, upper=999, step_increment=1, page_increment=5, page_size=0
            ),
            climb_rate=1,
            digits=0,
            valign=Gtk.Align.CENTER,
        )
        series_index_row = Adw.ActionRow(title="Series Index", subtitle="0 for none")
        series_index_row.add_suffix(self.series_index_spin)
        self.tags_entry = Adw.EntryRow(title="Tags (comma separated)")
        for row in (
            self.title_entry,
            self.author_entry,
            self.series_entry,
            series_index_row,
            self.tags_entry,
        ):
            meta_group.add(row)
        content.append(meta_group)

        desc_group = Adw.PreferencesGroup(title="Description")
        self.description_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD,
            height_request=100,
            top_margin=8,
            bottom_margin=8,
            left_margin=8,
            right_margin=8,
            css_classes=["card"],
        )
        desc_group.add(self.description_view)
        content.append(desc_group)

        formats_group = Adw.PreferencesGroup(title="Files &amp; Formats")
        self.formats_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"]
        )
        formats_group.add(self.formats_list)
        content.append(formats_group)

        reload_btn = Gtk.Button(
            label="Reload Details from File",
            halign=Gtk.Align.CENTER,
            margin_top=8,
            css_classes=["pill"],
            tooltip_text="Read title, author, series, description, tags and cover from the file",
        )
        reload_btn.connect(
            "clicked",
            lambda _btn: on_reload(self.current_book.book.id) if self.current_book else None,
        )
        content.append(reload_btn)

        delete_btn = Gtk.Button(
            label="Delete Book",
            halign=Gtk.Align.CENTER,
            margin_top=8,
            css_classes=["destructive-action", "pill"],
        )
        delete_btn.connect(
            "clicked",
            lambda _btn: on_delete(self.current_book.book.id) if self.current_book else None,
        )
        content.append(delete_btn)

        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=content
        )
        self.container.append(scroll)

    def set_send_targets(self, destinations: list[Destination]) -> None:
        """USB destinations plugged in right now, and email ones. The verb says the route."""
        while (child := self._send_box.get_first_child()) is not None:
            self._send_box.remove(child)
        for destination in destinations:
            if destination.kind == "email":
                label, icon, tip = (
                    f"Email to {destination.name}",
                    "mail-send-symbolic",
                    destination.email,
                )
            else:
                label, icon = f"Copy to {destination.name}", "media-removable-symbolic"
                tip = f"Over USB, into “{destination.books_folder}”"
            button = Gtk.Button(
                child=Adw.ButtonContent(label=label, icon_name=icon),
                tooltip_text=tip,
                css_classes=["pill"],  # plain: Save is the one suggested action
            )
            button.connect("clicked", lambda _b, rid=destination.id: self._send(rid))
            self._send_box.append(button)

    def _send(self, destination_id: str) -> None:
        if self.current_book is not None:
            self._on_send(self.current_book.book.id, destination_id)

    def set_reader_status(self, rows: list[ReaderStatus]) -> None:
        for row in self._status_rows:
            self._status_group.remove(row)
        self._status_rows.clear()
        for status in rows:
            row = Adw.ActionRow(title=status.title, subtitle=status.detail, use_markup=False)
            if status.action is not None:
                action, label = status.action
                button = Gtk.Button(
                    label=label,
                    valign=Gtk.Align.CENTER,
                    css_classes=["destructive-action"] if action == "delete" else [],
                )
                button.connect(
                    "clicked",
                    lambda _b, d=status.destination_id, a=action: self._reader_action(d, a),
                )
                row.add_suffix(button)
            self._status_group.add(row)
            self._status_rows.append(row)
        self._status_group.set_visible(bool(rows))

    def _reader_action(self, destination_id: str, action: str) -> None:
        if self.current_book is not None:
            self._on_reader_action(self.current_book.book.id, destination_id, action)

    def set_book(self, book_obj: BookObject | None) -> None:
        self.current_book = book_obj
        if book_obj is None:
            return
        book = book_obj.book

        while (child := self.cover_slot.get_first_child()) is not None:
            self.cover_slot.remove(child)
        texture = load_texture(book.cover_path)
        if texture is not None:
            cover: Gtk.Widget = Gtk.Picture(
                paintable=texture,
                height_request=240,
                can_shrink=True,
                content_fit=Gtk.ContentFit.SCALE_DOWN,
                css_classes=["card"],
            )
        else:
            cover = generated_cover(book, 168, 240)
            cover.add_css_class("card")
        self.cover_slot.append(cover)
        self.title_entry.set_text(book.title)
        self.author_entry.set_text(book.author)
        self.series_entry.set_text(book.series or "")
        self.series_index_spin.set_value(book.series_index if book.series_index is not None else 0)
        self.tags_entry.set_text(", ".join(book.tags))
        self._shown_description = html_to_text(book.description or "")
        self.description_view.get_buffer().set_text(self._shown_description)

        self.formats_list.remove_all()
        for file in book.files:
            row = Adw.ActionRow(
                title=file.format.upper(),
                subtitle=f"{file.file_size / (1024 * 1024):.2f} MB",
            )
            row.add_prefix(Gtk.Image(icon_name="document-open-symbolic"))
            self.formats_list.append(row)

    def save(self) -> None:
        self._save(None)

    def _save(self, _button: Gtk.Button | None) -> None:
        if self.current_book is None:
            return
        series = self.series_entry.get_text().strip() or None
        # 0 = no index; saving Some(0) would collide across index-less books
        spin_index = int(self.series_index_spin.get_value())
        series_index = spin_index if series is not None and spin_index > 0 else None

        buffer = self.description_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        if text == self._shown_description:
            description = self.current_book.book.description  # untouched: keep its HTML
        else:
            description = text if text.strip() else None

        tags = [t.strip() for t in self.tags_entry.get_text().split(",") if t.strip()]
        self._on_save(
            self.current_book.book.id,
            self.title_entry.get_text(),
            self.author_entry.get_text(),
            series,
            series_index,
            description,
            tags,
        )
