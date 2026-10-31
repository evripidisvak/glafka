import zlib
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from glafka.core.models import Book
from glafka.ui.book_object import BookObject
from glafka.ui.style import COVER_COLORS
from glafka.ui.thumbnails import thumbnails


def load_texture(path: str | None) -> Gdk.Texture | None:
    if not path or not Path(path).exists():
        return None
    try:
        return Gdk.Texture.new_from_file(Gio.File.new_for_path(path))
    except GLib.Error:
        return None


def generated_cover(book: Book, width: int, height: int, with_text: bool = True) -> Gtk.Widget:
    """Stand-in for a missing cover: title and author on a tile tinted per book."""
    # crc32, not hash(): Python's hash() changes every run, the colour must not
    color = zlib.crc32(f"{book.title}\0{book.author}".encode()) % len(COVER_COLORS)
    cover = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        width_request=width,
        height_request=height,
        vexpand=False,  # explicit: stops the author label's vexpand propagating to the tile
        css_classes=["generated-cover", f"cover-color-{color}"],
    )
    if not with_text:  # too small to read, e.g. list rows: the tint alone
        return cover
    text = dict(
        wrap=True,
        wrap_mode=Pango.WrapMode.WORD_CHAR,
        ellipsize=Pango.EllipsizeMode.END,
        justify=Gtk.Justification.CENTER,
        max_width_chars=1,  # natural width stays small; the label wraps to the tile instead
        margin_start=12,
        margin_end=12,
    )
    title = Gtk.Label(label=book.title, lines=5, margin_top=18, css_classes=["cover-title"], **text)
    author = Gtk.Label(
        label=book.author,
        lines=2,
        margin_bottom=14,
        valign=Gtk.Align.END,
        vexpand=True,
        css_classes=["cover-author"],
        **text,
    )
    cover.append(title)
    cover.append(author)
    return cover


def cover_widget(book: Book, width: int, height: int, with_text: bool = True) -> Gtk.Box:
    """The book's cover thumbnail, a placeholder until it is ready, or a generated cover."""
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        width_request=width,
        height_request=height,
        valign=Gtk.Align.CENTER,
        halign=Gtk.Align.CENTER,
    )

    def picture(texture: Gdk.Texture) -> Gtk.Widget:
        # A picture's natural width is its image's; clamp so every cover is `width` wide
        return Adw.Clamp(
            maximum_size=width,
            tightening_threshold=width,
            child=Gtk.Picture(
                paintable=texture,
                can_shrink=True,
                content_fit=Gtk.ContentFit.COVER,
                width_request=width,
                height_request=height,
            ),
        )

    def show(texture: Gdk.Texture | None) -> None:
        if box.get_root() is None:
            return  # tile or row reused for another book, or closed, since the request
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        box.append(picture(texture) if texture else generated_cover(book, width, height, with_text))

    if book.cover_path and Path(book.cover_path).exists():
        texture = thumbnails().load(Path(book.cover_path), show)
        if texture is not None:
            box.append(picture(texture))
        else:
            box.append(
                Gtk.Box(
                    width_request=width, height_request=height, css_classes=["cover-placeholder"]
                )
            )
    else:
        box.append(generated_cover(book, width, height, with_text))
    return box


def _series_text(book: Book) -> str | None:
    if book.series is None:
        return None
    return f"{book.series} #{book.series_index}" if book.series_index is not None else book.series


def _label(text: str, css: list[str]) -> Gtk.Label:
    return Gtk.Label(
        label=text,
        halign=Gtk.Align.START,
        ellipsize=Pango.EllipsizeMode.END,
        lines=1,
        css_classes=css,
    )


def create_book_card(item: BookObject) -> Gtk.Box:
    book = item.book
    container = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=6,
        width_request=140,
        margin_start=8,
        margin_end=8,
        margin_top=8,
        margin_bottom=8,
        css_classes=["card", "activatable"],
    )
    container.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

    cover = cover_widget(book, 140, 200)
    if item.on_reader:
        overlay = Gtk.Overlay(child=cover)  # the cover sets the size; the badge floats on it
        overlay.add_overlay(_reader_badge(margin=6))
        cover = overlay
    container.append(cover)

    # 2. Labels
    text_box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=2,
        margin_start=8,
        margin_end=8,
        margin_bottom=8,
    )
    text_box.append(_label(book.title, ["heading"]))
    text_box.append(_label(book.author, ["caption", "dim-label"]))

    if (series := _series_text(book)) is not None:
        text_box.append(_label(series, ["caption", "accent"]))

    formats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, margin_top=4)
    for fmt in item.formats:
        formats_box.append(Gtk.Label(label=fmt, css_classes=["caption", "tag", "numeric"]))
    text_box.append(formats_box)

    container.append(text_box)
    return container


def create_book_row(item: BookObject) -> Gtk.Box:
    """List-view row: small cover, title and author, series, formats."""
    book = item.book
    row = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
    row.append(cover_widget(book, 40, 58, with_text=False))

    text = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER
    )
    text.append(_label(book.title, ["heading"]))
    text.append(_label(book.author, ["caption", "dim-label"]))
    row.append(text)

    series = Gtk.Label(
        label=_series_text(book) or "",
        xalign=0,
        width_chars=22,
        max_width_chars=22,
        ellipsize=Pango.EllipsizeMode.END,
        css_classes=["caption", "accent"],
    )
    row.append(series)
    badge = _reader_badge(margin=0)
    badge.set_opacity(1 if item.on_reader else 0)  # keeps the columns aligned either way
    row.append(badge)
    row.append(
        Gtk.Label(
            label=" · ".join(item.formats),
            xalign=1,
            width_chars=12,
            css_classes=["caption", "numeric"],
        )
    )
    return row


def _reader_badge(margin: int) -> Gtk.Image:
    return Gtk.Image(
        icon_name="media-removable-symbolic",
        tooltip_text="On your reader",
        halign=Gtk.Align.END,
        valign=Gtk.Align.START if margin else Gtk.Align.CENTER,
        margin_top=margin,
        margin_end=margin,
        css_classes=["reader-badge"],
    )
