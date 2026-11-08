"""A picked image file as a book cover: upright, book-shaped if wanted, at most MAX_HEIGHT tall."""

import io

import cairo
from gi.repository import GdkPixbuf, Gtk, Pango, PangoCairo

from glafka.core.metadata import CoverImage
from glafka.core.models import Book
from glafka.ui.style import COVER_COLORS, cover_color

MAX_HEIGHT = 1600  # plenty for a reader's screen; keeps the EPUB small
RATIO = 2 / 3  # width / height of a typical ebook cover
WIDE = 0.8  # wider than this (width / height) and it doesn't look like a book cover


def load(path: str) -> GdkPixbuf.Pixbuf:
    """Upright (phone photos carry a rotation). Raises GLib.Error or ValueError."""
    if GdkPixbuf.Pixbuf.get_file_info(path)[0] is None:
        raise ValueError("not an image Glafka can read")
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    return pixbuf.apply_embedded_orientation() or pixbuf


def is_wide(pixbuf: GdkPixbuf.Pixbuf) -> bool:
    return pixbuf.get_width() / pixbuf.get_height() > WIDE


def crop(pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
    """The middle of a wide image, book-shaped, at most MAX_HEIGHT tall."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    cut = min(width, round(height * RATIO))
    middle = pixbuf.new_subpixbuf((width - cut) // 2, 0, cut, height)
    return _fit_height(middle, MAX_HEIGHT)


def fit(pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
    """The whole image centred on a book-shaped cover, in the image's own (darkened) colour.

    The cover is as wide as the image (capped), so a small image isn't lost in a big cover.
    """
    canvas_w = min(round(MAX_HEIGHT * RATIO), pixbuf.get_width())
    canvas_h = round(canvas_w / RATIO)  # taller than any wide image of that width
    image = _scaled(pixbuf, canvas_w / pixbuf.get_width())  # never up: canvas_w <= width
    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, canvas_w, canvas_h)
    canvas.fill(_background(pixbuf))
    width, height = image.get_width(), image.get_height()
    y = (canvas_h - height) // 2
    image.composite(canvas, 0, y, width, height, 0, y, 1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
    return canvas


def generated(book: Book) -> GdkPixbuf.Pixbuf:
    """The in-app generated cover as a full-size image: the 140x200 tile's layout, scaled up."""
    width, height = round(MAX_HEIGHT * RATIO), MAX_HEIGHT
    unit = width / 140  # measurements below are the tile's, in its pixels
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
    cr = cairo.Context(surface)
    colour = COVER_COLORS[cover_color(book.title, book.author)]
    cr.set_source_rgb(*(int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)))
    cr.paint()
    family, text_px = _ui_font(), 14.67 * unit  # GTK's 11 pt at 96 dpi
    margin = 12 * unit
    title = _layout(cr, book.title, family, text_px, Pango.Weight.ULTRABOLD, 5, width - 2 * margin)
    cr.set_source_rgb(1, 1, 1)
    cr.move_to(margin, 18 * unit)
    PangoCairo.show_layout(cr, title)
    author = _layout(
        cr, book.author, family, 0.9 * text_px, Pango.Weight.NORMAL, 2, width - 2 * margin
    )
    cr.set_source_rgba(1, 1, 1, 0.85)
    cr.move_to(margin, height - 14 * unit - author.get_pixel_size()[1])
    PangoCairo.show_layout(cr, author)
    png = io.BytesIO()
    surface.write_to_png(png)
    loader = GdkPixbuf.PixbufLoader()
    loader.write(png.getvalue())
    loader.close()
    return loader.get_pixbuf()


def encode(pixbuf: GdkPixbuf.Pixbuf, png: bool = False) -> CoverImage:
    """At most MAX_HEIGHT tall (never scaled up); JPEG puts transparency on white."""
    pixbuf = _fit_height(pixbuf, MAX_HEIGHT)
    if png:
        _ok, data = pixbuf.save_to_bufferv("png", [], [])
        return CoverImage(mime_type="image/png", data=bytes(data))
    if pixbuf.get_has_alpha():
        width, height = pixbuf.get_width(), pixbuf.get_height()
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
        flat.fill(0xFFFFFFFF)
        pixbuf.composite(flat, 0, 0, width, height, 0, 0, 1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
        pixbuf = flat
    _ok, data = pixbuf.save_to_bufferv("jpeg", ["quality"], ["90"])
    return CoverImage(mime_type="image/jpeg", data=bytes(data))


def preview_png(pixbuf: GdkPixbuf.Pixbuf, height: int = 180) -> bytes:
    """Small PNG of a candidate cover, for the dialog."""
    _ok, data = _fit_height(pixbuf, height).save_to_bufferv("png", [], [])
    return bytes(data)


def _background(pixbuf: GdkPixbuf.Pixbuf) -> int:
    """The image's average colour, darkened a little so the picture stands out; as 0xRRGGBBAA."""
    dot = pixbuf.scale_simple(1, 1, GdkPixbuf.InterpType.HYPER)  # HYPER averages properly
    r, g, b = dot.get_pixels()[:3]
    r, g, b = (round(c * 0.7) for c in (r, g, b))
    return (r << 24) | (g << 16) | (b << 8) | 0xFF


def _fit_height(pixbuf: GdkPixbuf.Pixbuf, height: int) -> GdkPixbuf.Pixbuf:
    if pixbuf.get_height() <= height:
        return pixbuf
    return _scaled(pixbuf, height / pixbuf.get_height())


def _scaled(pixbuf: GdkPixbuf.Pixbuf, factor: float) -> GdkPixbuf.Pixbuf:
    if factor == 1.0:
        return pixbuf
    width = max(1, round(pixbuf.get_width() * factor))
    height = max(1, round(pixbuf.get_height() * factor))
    return pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.HYPER)


def _ui_font() -> str:
    """The desktop's interface font family; "Sans" without a display (tests)."""
    settings = Gtk.Settings.get_default()
    name = settings.get_property("gtk-font-name") if settings else None
    return Pango.FontDescription.from_string(name).get_family() if name else "Sans"


def _layout(
    cr: cairo.Context,
    text: str,
    family: str,
    px: float,
    weight: Pango.Weight,
    lines: int,
    width: float,
) -> Pango.Layout:
    """Centred, wrapped, at most `lines` lines with an ellipsis: like the tile's labels."""
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription()
    font.set_family(family)
    font.set_weight(weight)
    font.set_absolute_size(px * Pango.SCALE)
    layout.set_font_description(font)
    layout.set_width(round(width * Pango.SCALE))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_height(-lines)  # negative: a number of lines
    layout.set_alignment(Pango.Alignment.CENTER)
    layout.set_text(text, -1)
    return layout
