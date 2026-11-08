"""Small GdkPixbuf helpers shared by thumbnails and covers."""

from gi.repository import GdkPixbuf


def flatten_on_white(pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
    """Transparency onto white, for JPEG."""
    if not pixbuf.get_has_alpha():
        return pixbuf
    width, height = pixbuf.get_width(), pixbuf.get_height()
    flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    flat.fill(0xFFFFFFFF)
    pixbuf.composite(flat, 0, 0, width, height, 0, 0, 1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
    return flat
