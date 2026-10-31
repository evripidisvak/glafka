"""Thumbnail cache, with a real GLib main loop (no display needed)."""

import os

import pytest
from gi.repository import GdkPixbuf, GLib

from glafka.ui.thumbnails import (
    THUMB_HEIGHT,
    THUMB_WIDTH,
    ThumbnailLoader,
    make_thumbnail,
    thumbnail_path,
)


def make_image(path, width, height, alpha=False, fmt="jpeg"):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, alpha, 8, width, height)
    pixbuf.fill(0x3366CC80 if alpha else 0x3366CCFF)
    pixbuf.savev(str(path), fmt, [], [])
    return path


def pump_until(condition, timeout=5.0):
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(timeout * 1e6)
    while not condition() and GLib.get_monotonic_time() < deadline:
        context.iteration(False)
    assert condition(), "timed out"


@pytest.fixture
def loader(tmp_path):
    loader = ThumbnailLoader(tmp_path / "cache")
    yield loader
    loader.shutdown()


def test_thumbnail_fits_the_tile_and_keeps_aspect(tmp_path):
    cover = make_image(tmp_path / "cover.jpg", 1200, 1800)
    dest = tmp_path / "thumb.jpg"

    make_thumbnail(cover, dest)

    _fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(str(dest))
    assert (width, height) == (round(THUMB_HEIGHT * 1200 / 1800), THUMB_HEIGHT)
    assert width <= THUMB_WIDTH


def test_transparent_png_cover_becomes_jpeg(tmp_path):
    cover = make_image(tmp_path / "cover.png", 300, 450, alpha=True, fmt="png")
    dest = tmp_path / "thumb.jpg"

    make_thumbnail(cover, dest)

    assert GdkPixbuf.Pixbuf.get_file_info(str(dest))[0].get_name() == "jpeg"


def test_cache_key_changes_when_the_cover_changes(tmp_path):
    cover = make_image(tmp_path / "cover.jpg", 100, 150)
    before = thumbnail_path(cover, tmp_path)
    os.utime(cover, ns=(0, 1_000_000_000))

    assert thumbnail_path(cover, tmp_path) != before


def test_missing_thumbnail_is_made_once_then_cached(tmp_path, loader):
    cover = make_image(tmp_path / "cover.jpg", 800, 1200)
    delivered = []

    first = loader.load(cover, delivered.append)
    second = loader.load(cover, delivered.append)  # same cover in another tile
    pump_until(lambda: len(delivered) == 2)

    assert first is None and second is None
    assert all(t is not None and t.get_height() == THUMB_HEIGHT for t in delivered)
    assert len(list((tmp_path / "cache").glob("*.jpg"))) == 1
    assert loader.load(cover, delivered.append) is not None  # now straight from the cache


def test_unreadable_cover_reports_none(tmp_path, loader):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"not an image")
    delivered = []

    assert loader.load(cover, delivered.append) is None
    pump_until(lambda: len(delivered) == 1)

    assert delivered == [None]


def test_prune_keeps_only_current_covers(tmp_path, loader):
    kept_cover = make_image(tmp_path / "kept.jpg", 300, 450)
    gone_cover = make_image(tmp_path / "gone.jpg", 300, 450)
    delivered = []
    loader.load(kept_cover, delivered.append)
    loader.load(gone_cover, delivered.append)
    pump_until(lambda: len(delivered) == 2)
    cache = tmp_path / "cache"
    assert len(list(cache.glob("*.jpg"))) == 2

    loader.prune([kept_cover])  # gone.jpg's book was deleted
    pump_until(lambda: len(list(cache.glob("*.jpg"))) == 1)

    assert list(cache.glob("*.jpg")) == [thumbnail_path(kept_cover, cache)]
