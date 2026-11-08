"""A picked image file as a book cover: upright, book-shaped if wanted, at most MAX_HEIGHT tall."""

import sys
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from gi.repository import GdkPixbuf, GLib, Gtk, Pango

from glafka.core.errors import GlafkaError
from glafka.core.metadata import CoverImage
from glafka.core.models import Book
from glafka.ui.pixbufs import flatten_on_white
from glafka.ui.style import COVER_COLORS, cover_color

if TYPE_CHECKING:
    import cairo

MAX_HEIGHT = 1600  # plenty for a reader's screen; keeps the EPUB small
LOAD_MAX = 2400  # big photos are decoded at this size: every later step stays quick
RATIO = 2 / 3  # width / height of a typical ebook cover
WIDE = 0.8  # wider than this (width / height) and it doesn't look like a book cover


def load(path: str) -> GdkPixbuf.Pixbuf:
    """Upright (phone photos carry a rotation). Raises GlafkaError. Safe off the main thread."""
    found, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
    if found is None:
        raise GlafkaError("not an image Glafka can read")
    try:
        if max(width, height) > LOAD_MAX:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, LOAD_MAX, LOAD_MAX, True)
        else:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    except GLib.Error as e:
        raise GlafkaError(e.message) from e
    return pixbuf.apply_embedded_orientation() or pixbuf


def is_wide(pixbuf: GdkPixbuf.Pixbuf) -> bool:
    return pixbuf.get_width() / pixbuf.get_height() > WIDE


def crop(pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
    """The middle of a wide image, book-shaped, at most MAX_HEIGHT tall."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    cut = min(width, round(height * RATIO))
    middle = pixbuf.new_subpixbuf((width - cut) // 2, 0, cut, height)
    return fit_height(middle, MAX_HEIGHT)


def fit(pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
    """The whole image centred on a 2:3 cover as wide as the image (capped), in its own colour."""
    canvas_w = min(round(MAX_HEIGHT * RATIO), pixbuf.get_width())
    canvas_h = round(canvas_w / RATIO)  # taller than any wide image of that width
    image = _scaled(pixbuf, canvas_w / pixbuf.get_width())  # never up: canvas_w <= width
    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, canvas_w, canvas_h)
    canvas.fill(_background(pixbuf))
    width, height = image.get_width(), image.get_height()
    y = (canvas_h - height) // 2
    image.composite(canvas, 0, y, width, height, 0, y, 1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
    return canvas


def generated(book: Book, family: str) -> GdkPixbuf.Pixbuf:
    """The tile's generated cover drawn at full size; `family` from ui_font() (main thread)."""
    import cairo  # only needed here
    from gi.repository import PangoCairo

    width, height = round(MAX_HEIGHT * RATIO), MAX_HEIGHT
    unit = width / 140  # measurements below are the tile's, in its pixels
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
    cr = cairo.Context(surface)
    colour = COVER_COLORS[cover_color(book.title, book.author)]
    cr.set_source_rgb(*(int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)))
    cr.paint()
    text_px = 14.67 * unit  # GTK's 11 pt at 96 dpi
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
    surface.flush()
    return _rgb_pixbuf(bytes(surface.get_data()), width, height, surface.get_stride())


def encode(pixbuf: GdkPixbuf.Pixbuf, png: bool = False) -> CoverImage:
    """At most MAX_HEIGHT tall (never scaled up); JPEG puts transparency on white."""
    pixbuf = fit_height(pixbuf, MAX_HEIGHT)
    if png:
        _ok, data = pixbuf.save_to_bufferv("png", [], [])
        return CoverImage("image/png", bytes(data), pixbuf.get_width(), pixbuf.get_height())
    pixbuf = flatten_on_white(pixbuf)
    _ok, data = pixbuf.save_to_bufferv("jpeg", ["quality"], ["90"])
    return CoverImage("image/jpeg", bytes(data), pixbuf.get_width(), pixbuf.get_height())


class LazyCovers(Mapping[str, CoverImage]):
    """One image as JPEG and PNG, each encoded only when the library asks for that format."""

    TYPES = ("image/jpeg", "image/png")

    def __init__(self, pixbuf: GdkPixbuf.Pixbuf) -> None:
        self._pixbuf = fit_height(pixbuf, MAX_HEIGHT)  # scaled once for both formats
        self._encoded: dict[str, CoverImage] = {}

    def __getitem__(self, media_type: str) -> CoverImage:
        if media_type not in self.TYPES:
            raise KeyError(media_type)
        if media_type not in self._encoded:
            self._encoded[media_type] = encode(self._pixbuf, png=media_type == "image/png")
        return self._encoded[media_type]

    def __iter__(self) -> Iterator[str]:
        return iter(self.TYPES)

    def __len__(self) -> int:
        return len(self.TYPES)


def _rgb_pixbuf(data: bytes, width: int, height: int, stride: int) -> GdkPixbuf.Pixbuf:
    """Cairo's RGB24 (B, G, R, unused on little-endian machines) to an RGB pixbuf."""
    rows = [data[y * stride : y * stride + width * 4] for y in range(height)]
    pixels = b"".join(rows) if stride != width * 4 else data[: width * height * 4]
    b, g, r = (0, 1, 2) if sys.byteorder == "little" else (3, 2, 1)
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = pixels[r::4], pixels[g::4], pixels[b::4]
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(rgb)), GdkPixbuf.Colorspace.RGB, False, 8, width, height, width * 3
    )


def preview_png(pixbuf: GdkPixbuf.Pixbuf, height: int = 180) -> bytes:
    """Small PNG of a candidate cover, for the dialog."""
    _ok, data = fit_height(pixbuf, height).save_to_bufferv("png", [], [])
    return bytes(data)


def _background(pixbuf: GdkPixbuf.Pixbuf) -> int:
    """The image's average colour, darkened a little so the picture stands out; as 0xRRGGBBAA."""
    dot = pixbuf.scale_simple(1, 1, GdkPixbuf.InterpType.BILINEAR)  # averages when shrinking
    r, g, b = dot.get_pixels()[:3]
    r, g, b = (round(c * 0.7) for c in (r, g, b))
    return (r << 24) | (g << 16) | (b << 8) | 0xFF


def fit_height(pixbuf: GdkPixbuf.Pixbuf, height: int) -> GdkPixbuf.Pixbuf:
    """At most `height` tall, never scaled up."""
    if pixbuf.get_height() <= height:
        return pixbuf
    return _scaled(pixbuf, height / pixbuf.get_height())


def _scaled(pixbuf: GdkPixbuf.Pixbuf, factor: float) -> GdkPixbuf.Pixbuf:
    if factor == 1.0:
        return pixbuf
    width = max(1, round(pixbuf.get_width() * factor))
    height = max(1, round(pixbuf.get_height() * factor))
    return pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)


def ui_font() -> str:
    """The desktop's interface font family; "Sans" without a display (tests)."""
    settings = Gtk.Settings.get_default()
    name = settings.get_property("gtk-font-name") if settings else None
    return Pango.FontDescription.from_string(name).get_family() if name else "Sans"


def _layout(
    cr: "cairo.Context",
    text: str,
    family: str,
    px: float,
    weight: Pango.Weight,
    lines: int,
    width: float,
) -> Pango.Layout:
    """Centred, wrapped, at most `lines` lines with an ellipsis: like the tile's labels."""
    from gi.repository import PangoCairo

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
