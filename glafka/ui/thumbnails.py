import contextlib
import hashlib
import logging
import os
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gi.repository import Gdk, GdkPixbuf, GLib

from glafka.core.config import LEGACY_NAME
from glafka.ui.pixbufs import flatten_on_white

log = logging.getLogger(__name__)

THUMB_WIDTH, THUMB_HEIGHT = 280, 400  # twice the 140x200 tile: sharp on HiDPI screens

OnReady = Callable[[Gdk.Texture | None], None]


def cache_dir() -> Path:
    return Path(GLib.get_user_cache_dir()) / "glafka" / "thumbnails"


def remove_legacy_cache() -> None:
    """Thumbnails cached under the app's old name: only ever our own folder."""
    old = Path(GLib.get_user_cache_dir()) / LEGACY_NAME
    shutil.rmtree(old / "thumbnails", ignore_errors=True)
    with contextlib.suppress(OSError):
        old.rmdir()  # only if nothing else is in there


def thumbnail_path(cover: Path, directory: Path) -> Path | None:
    """Cache file for the cover's current content (changes when the cover does); None if gone."""
    try:
        st = cover.stat()
    except OSError:
        return None
    key = f"{cover.absolute()}\0{st.st_size}\0{st.st_mtime_ns}"
    return directory / f"{hashlib.sha1(key.encode()).hexdigest()}.jpg"


def make_thumbnail(cover: Path, dest: Path) -> None:
    """Scale the cover to fit THUMB_WIDTH x THUMB_HEIGHT and save it as JPEG."""
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(cover), THUMB_WIDTH, THUMB_HEIGHT, True)
    pixbuf = flatten_on_white(pixbuf)  # JPEG has no alpha
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{threading.get_ident()}.tmp")
    pixbuf.savev(str(tmp), "jpeg", ["quality"], ["85"])
    os.replace(tmp, dest)  # atomic: a reader never sees a half-written thumbnail


class ThumbnailLoader:
    """Cover thumbnails for tiles: cached on disk, missing ones made on background threads.

    Its own small pool, not the library worker, so a long import doesn't hold up covers.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or cache_dir()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="glafka-thumbs")
        self._waiting: dict[Path, list[OnReady]] = {}  # main thread only
        self._failed: set[Path] = set()  # unreadable covers: don't retry every redraw

    def load(self, cover: Path, on_ready: OnReady) -> Gdk.Texture | None:
        """The thumbnail now if cached; else None, and on_ready(texture or None) later."""
        thumb = thumbnail_path(cover, self._dir)
        if thumb is None or thumb in self._failed:
            _later(on_ready, None)
            return None
        if thumb.exists():
            try:
                return Gdk.Texture.new_from_filename(str(thumb))
            except GLib.Error:
                thumb.unlink(missing_ok=True)  # damaged cache entry: make it again
        callbacks = self._waiting.setdefault(thumb, [])
        callbacks.append(on_ready)
        if len(callbacks) == 1:  # the same cover in several tiles is made once
            self._executor.submit(self._make, cover, thumb)
        return None

    def prune(self, covers: list[Path]) -> None:
        """Delete cached thumbnails of anything but these covers (deleted, moved, changed)."""
        self._executor.submit(self._prune, list(covers))

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _prune(self, covers: list[Path]) -> None:  # thumbnail thread
        keep = {thumb for cover in covers if (thumb := thumbnail_path(cover, self._dir))}
        removed = 0
        for path in self._dir.glob("*.jpg"):  # finished thumbnails only, never a .tmp in progress
            if path not in keep:
                with contextlib.suppress(OSError):
                    path.unlink()
                    removed += 1
        if removed:
            log.info("Removed %d unused cover thumbnail(s)", removed)

    def _make(self, cover: Path, thumb: Path) -> None:  # thumbnail thread
        try:
            make_thumbnail(cover, thumb)
            ok = True
        except (GLib.Error, OSError) as e:
            log.warning("Could not make a thumbnail of %s: %s", cover, e)
            ok = False
        GLib.idle_add(self._deliver, thumb, ok)

    def _deliver(self, thumb: Path, ok: bool) -> bool:  # main thread
        texture = None
        if ok:
            try:
                texture = Gdk.Texture.new_from_filename(str(thumb))
            except GLib.Error as e:
                log.warning("Could not load thumbnail %s: %s", thumb, e)
        if texture is None:
            self._failed.add(thumb)
        for on_ready in self._waiting.pop(thumb, []):
            on_ready(texture)
        return GLib.SOURCE_REMOVE


def _later(fn: OnReady, texture: Gdk.Texture | None) -> None:
    """Callbacks always come later, never during load(): one code path for callers."""

    def call() -> bool:
        fn(texture)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(call)


_loader: ThumbnailLoader | None = None


def thumbnails() -> ThumbnailLoader:
    """The app-wide loader (main thread)."""
    global _loader
    if _loader is None:
        _loader = ThumbnailLoader()
    return _loader
