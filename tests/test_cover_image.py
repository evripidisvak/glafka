"""Turning picked images into covers. Fixtures are drawn with GdkPixbuf itself."""

import pytest
from gi.repository import GdkPixbuf

from glafka.ui import cover_image


def image(width, height, *bands, alpha=False):
    """Vertical colour bands (0xRRGGBBAA), equal widths, left to right."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, alpha, 8, width, height)
    step = width // len(bands)
    for i, colour in enumerate(bands):
        pixbuf.new_subpixbuf(
            i * step, 0, step if i < len(bands) - 1 else width - i * step, height
        ).fill(colour)
    return pixbuf


def pixel(pixbuf, x, y):
    data, stride, channels = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_n_channels()
    return tuple(data[y * stride + x * channels : y * stride + x * channels + 3])


def decode(cover):
    loader = GdkPixbuf.PixbufLoader()
    loader.write(cover.data)
    loader.close()
    return loader.get_pixbuf()


@pytest.mark.parametrize(
    ("size", "wide"), [((900, 600), True), ((600, 900), False), ((800, 1000), False)]
)
def test_wide_means_wider_than_0_8(size, wide):
    assert cover_image.is_wide(image(*size, 0x000000FF)) is wide


def test_crop_takes_the_middle_book_shaped():
    red, green, blue = 0xFF0000FF, 0x00FF00FF, 0x0000FFFF
    cropped = cover_image.crop(image(3000, 1000, red, green, blue))

    assert (cropped.get_width(), cropped.get_height()) == (667, 1000)
    assert pixel(cropped, 0, 500) == pixel(cropped, 666, 500) == (0, 255, 0)


def test_fit_keeps_the_whole_image_on_its_darkened_average_colour():
    fitted = cover_image.fit(image(1200, 600, 0xC86432FF))  # (200, 100, 50)

    assert (fitted.get_width(), fitted.get_height()) == (1067, 1600)  # capped, book-shaped
    assert pixel(fitted, 10, 10) == (140, 70, 35)  # background: 70% of the average
    assert pixel(fitted, 533, 800) == (200, 100, 50)  # the image itself, centred


def test_fit_gives_a_small_image_a_small_cover():
    fitted = cover_image.fit(image(400, 300, 0x808080FF))

    assert (fitted.get_width(), fitted.get_height()) == (400, 600)


def test_encode_scales_down_but_never_up():
    assert decode(cover_image.encode(image(500, 2000, 0x808080FF))).get_height() == 1600
    assert decode(cover_image.encode(image(200, 300, 0x808080FF))).get_height() == 300


def test_encode_puts_transparency_on_white_for_jpeg_and_keeps_png_on_request():
    clear = image(200, 300, 0x00000000, alpha=True)

    jpeg = cover_image.encode(clear)
    png = cover_image.encode(clear, png=True)

    assert jpeg.mime_type == "image/jpeg" and pixel(decode(jpeg), 5, 5) == (255, 255, 255)
    assert png.mime_type == "image/png" and decode(png).get_has_alpha()


def test_generated_cover_is_the_tile_drawn_large_in_the_book_s_colour():
    from glafka.core.models import Book
    from glafka.ui.style import COVER_COLORS, cover_color

    book = Book("b", "The Secret Place", "Tana French")
    drawn = cover_image.generated(book, cover_image.ui_font())

    hex_colour = COVER_COLORS[cover_color(book.title, book.author)]
    rgb = tuple(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    assert (drawn.get_width(), drawn.get_height()) == (1067, 1600)
    assert pixel(drawn, 5, 800) == rgb  # background away from the text

    data, stride, n = drawn.get_pixels(), drawn.get_rowstride(), drawn.get_n_channels()

    def has_text(top, bottom):  # white glyph pixels somewhere in that band
        return any(
            min(data[y * stride + x * n : y * stride + x * n + 3]) > 200
            for y in range(top, bottom, 4)
            for x in range(80, 990, 4)
        )

    assert has_text(130, 400)  # title near the top
    assert has_text(1350, 1500)  # author near the bottom
    assert not has_text(700, 900)  # nothing in the middle


def test_big_photos_are_loaded_scaled_down(tmp_path):
    path = tmp_path / "big.png"
    image(5000, 3000, 0x808080FF).savev(str(path), "png", [], [])

    loaded = cover_image.load(str(path))

    assert (loaded.get_width(), loaded.get_height()) == (2400, 1440)


def test_encoded_covers_carry_their_size():
    cover = cover_image.encode(image(500, 2000, 0x808080FF))

    assert (cover.width, cover.height) == (400, 1600)


def test_a_file_that_is_no_image_is_a_clean_error(tmp_path):
    from glafka.core.errors import GlafkaError

    path = tmp_path / "notes.txt"
    path.write_text("not an image")

    with pytest.raises(GlafkaError):
        cover_image.load(str(path))


def test_lazy_covers_offer_jpeg_and_png_but_encode_only_what_is_asked():
    covers = cover_image.LazyCovers(image(200, 3000, 0x808080FF))

    jpeg = covers["image/jpeg"]

    assert set(covers) == {"image/jpeg", "image/png"}
    assert (jpeg.mime_type, jpeg.width, jpeg.height) == ("image/jpeg", 107, 1600)  # scaled once
    assert set(covers._encoded) == {"image/jpeg"}  # no PNG made until something asks for it
    assert covers["image/png"].mime_type == "image/png"
