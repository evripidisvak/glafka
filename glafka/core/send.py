"""Copy books to USB e-readers. Emailing them is in `mail.py`."""

import contextlib
import os
import shutil
import uuid
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path, PurePosixPath

from glafka.core.db import Database
from glafka.core.errors import GlafkaError, NotFoundError
from glafka.core.importer import compute_sha256
from glafka.core.models import Book, BookFile, Destination, DeviceCopy, SentRecord, utc_now

_FAT_INVALID = set('\\/:*?"<>|')
_MAX_FOLDER, _MAX_STEM = 80, 100  # keep whole paths well inside FAT and reader limits
_NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
_SPACE_MARGIN = 1024 * 1024  # leave the device a little room


class SendStatus(Enum):
    SENT = auto()
    ALREADY_THERE = auto()  # an identical file is already on the reader
    DIFFERENT_COPY_THERE = auto()  # another file has that name; replaced only if asked
    NO_SUITABLE_FORMAT = auto()  # the book has no format the destination takes


@dataclass
class SendResult:
    status: SendStatus
    book: Book
    format: str | None = None
    device_path: Path | None = None


def author_sort(authors: str) -> str:
    """Calibre's default sort name, per author: 'Asako Yuzuki & B C' -> 'Yuzuki, Asako & C, B'.

    Like Calibre's default, particles aren't recognised: 'Ursula K. Le Guin' -> 'Guin, Ursula K. Le'
    (only the folder name on the device; the reader shows the book's own metadata).
    """
    names = [n.strip() for n in authors.split("&") if n.strip()]
    return " & ".join(_invert(n) for n in names) or "Unknown"


def _invert(name: str) -> str:
    if name == "Unknown Author":
        return "Unknown"  # where Calibre puts books without an author
    if "," in name:
        return name  # already "Last, First"
    tokens = name.split()
    if len(tokens) < 2:
        return name
    suffix = ""
    if tokens[-1].lower() in _NAME_SUFFIXES and len(tokens) > 2:
        suffix = f", {tokens.pop()}"
    return f"{tokens[-1]}, {' '.join(tokens[:-1])}{suffix}"


def fat_name(name: str, limit: int) -> str:
    """One path component FAT accepts and keeps exactly (it drops trailing dots and spaces)."""
    cleaned = "".join("_" if c in _FAT_INVALID or ord(c) < 32 else c for c in name)
    return cleaned[:limit].rstrip(" .") or "_"


def file_name(book: Book, fmt: str) -> str:
    """Calibre's file name, FAT-safe: '<Title> - <Author>.<ext>'."""
    author = "Unknown" if book.author == "Unknown Author" else book.author
    return f"{fat_name(f'{book.title} - {author}', _MAX_STEM)}.{fmt}"


def device_path(book: Book, fmt: str, books_folder: str) -> PurePosixPath:
    """Calibre's PocketBook layout: <books folder>/<Last, First>/<Title> - <Author>.<ext>."""
    folder = fat_name(author_sort(book.author), _MAX_FOLDER)
    return PurePosixPath(books_folder) / folder / file_name(book, fmt)


def choose_format(book: Book, destination: Destination) -> BookFile | None:
    """The book's file in the destination's most preferred format, if it has one."""
    by_format = {f.format: f for f in book.files}
    return next((by_format[fmt] for fmt in destination.formats if fmt in by_format), None)


def send_to_usb(
    db: Database, book_id: str, destination: Destination, mount_root: Path, replace: bool = False
) -> SendResult:
    """Copy the book to a mounted reader. Never replaces a different file unless `replace`."""
    book = db.get_book_by_id(book_id)
    if book is None:
        raise NotFoundError(f"Book '{book_id}' not found")
    if not mount_root.is_dir():
        raise GlafkaError(f"{destination.name} is not connected")
    file = choose_format(book, destination)
    if file is None:
        return SendResult(SendStatus.NO_SUITABLE_FORMAT, book)

    source = Path(file.file_path)
    dest = mount_root / device_path(book, file.format, destination.books_folder)
    if dest.exists():
        # Size first: hashing a large file over USB is slow
        if dest.stat().st_size == file.file_size and compute_sha256(dest) == file.sha256:
            log_sent(db, book, destination, file, "already-there")
            _on_device(db, book, destination, mount_root, dest)
            return SendResult(SendStatus.ALREADY_THERE, book, file.format, dest)
        if not replace:
            return SendResult(SendStatus.DIFFERENT_COPY_THERE, book, file.format, dest)

    if shutil.disk_usage(mount_root).free < source.stat().st_size + _SPACE_MARGIN:
        raise GlafkaError(f"Not enough space on {destination.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _copy_and_sync(source, dest)
    log_sent(db, book, destination, file, "sent")
    _on_device(db, book, destination, mount_root, dest)
    return SendResult(SendStatus.SENT, book, file.format, dest)


def _copy_and_sync(source: Path, dest: Path) -> None:
    """Hidden .part file (no ebook extension, so the reader won't index it), then swapped in."""
    tmp = dest.with_name(f".{uuid.uuid4().hex}.part")
    try:
        with open(source, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())  # on the device, not just in the page cache
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    with contextlib.suppress(OSError):  # vfat may refuse fsync on a directory (EINVAL)
        fd = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def log_sent(
    db: Database, book: Book, destination: Destination, file: BookFile, status: str
) -> None:
    db.log_sent(
        SentRecord(
            id=str(uuid.uuid4()),
            book_id=book.id,
            target_type=destination.kind,
            target_name=destination.name,
            format=file.format,
            sent_at=utc_now(),
            status=status,
            destination_id=destination.id,
            sha256=file.sha256,
            file_size=file.file_size,
        )
    )


def _on_device(
    db: Database, book: Book, destination: Destination, mount_root: Path, dest: Path
) -> None:
    path = dest.relative_to(mount_root).as_posix()
    db.add_device_copy(DeviceCopy(destination.id, book.id, path))
