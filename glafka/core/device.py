"""Library books on a USB reader: find them on its drive, delete them from it.

Reads the whole drive; writes only inside the destination's books folder.
"""

import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from glafka.core.db import Database
from glafka.core.errors import GlafkaError
from glafka.core.history import record
from glafka.core.importer import compute_sha256
from glafka.core.metadata.epub import find_opf_path, parse_opf
from glafka.core.models import BookFilter, BookSort, Destination, DeviceCopy, HistoryAction, utc_now

# The reader's own folders; hidden ones (.Trash-1000, .adobe-digital-editions) are skipped too
_SKIP_DIRS = {"system", "System Volume Information", "$RECYCLE.BIN"}
_INVISIBLE = re.compile(r"[​-‍⁠﻿]")


@dataclass
class ScanResult:
    copies: list[DeviceCopy]
    other_files: int  # ebook files that are not library books

    @property
    def book_ids(self) -> set[str]:
        return {c.book_id for c in self.copies}


@dataclass
class RemoveResult:
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # outside the books folder: never deleted
    failed: list[str] = field(default_factory=list)


def scan_device(db: Database, destination: Destination, mount_root: Path) -> ScanResult:
    """Find library books anywhere on the drive; the result replaces what was known."""
    if not mount_root.is_dir():
        raise GlafkaError(f"{destination.name} is not connected")
    by_size: dict[int, list[tuple[str, str, str]]] = {}  # size -> (book id, format, sha256)
    for book_id, fmt, sha, size in db.known_copies():
        by_size.setdefault(size, []).append((book_id, fmt, sha))
    formats = {fmt for candidates in by_size.values() for _, fmt, _ in candidates}
    by_title = _epub_books_by_title(db)

    now = utc_now()
    copies: list[DeviceCopy] = []
    other = 0
    for path in _ebook_files(mount_root, formats):
        book_ids = _match(path, by_size, by_title)
        if not book_ids:
            other += 1
        relative = path.relative_to(mount_root).as_posix()
        copies += [DeviceCopy(destination.id, b, relative, now) for b in sorted(book_ids)]
    db.replace_device_copies(destination.id, copies, now)
    return ScanResult(copies, other)


def remove_from_device(
    db: Database, book_id: str, destination: Destination, mount_root: Path
) -> RemoveResult:
    """Delete the book's copies in the books folder; copies elsewhere are only reported."""
    if destination.kind != "usb" or not destination.books_folder.strip("/"):
        raise GlafkaError(f"Glafka has no books folder on {destination.name}")
    if not mount_root.is_dir():
        raise GlafkaError(f"{destination.name} is not connected")
    books_root = Path(os.path.realpath(mount_root / destination.books_folder))
    if not _strictly_under(books_root, Path(os.path.realpath(mount_root))):
        raise GlafkaError(f"The books folder of {destination.name} is not on the reader")

    result = RemoveResult()
    for copy in db.device_copies(book_id):
        if copy.destination_id != destination.id:
            continue
        path = Path(os.path.realpath(mount_root / copy.path))
        if not _strictly_under(path, books_root):
            result.kept.append(copy.path)
            continue
        try:
            path.unlink(missing_ok=True)  # already gone: just forget it
        except OSError:
            result.failed.append(copy.path)
            continue
        result.removed.append(copy.path)
        _remove_empty_folders(path.parent, books_root)
    db.remove_device_copies(destination.id, book_id, result.removed)
    book = db.get_book_by_id(book_id)
    if book is not None:
        for path in result.removed:
            record(db, book, destination, HistoryAction.DELETED, fmt=Path(path).suffix[1:].lower())
    return result


def _ebook_files(root: Path, formats: set[str]):
    for folder, dirs, files in os.walk(root):  # symlinks are not followed
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
        for name in files:
            if not name.startswith(".") and name.rpartition(".")[2].lower() in formats:
                yield Path(folder) / name


def _match(
    path: Path,
    by_size: dict[int, list[tuple[str, str, str]]],
    by_title: dict[str, list[tuple[str, str]]],
) -> set[str]:
    """Same content as a known copy; else, for EPUBs, same title and first author."""
    fmt = path.suffix[1:].lower()
    try:
        candidates = [c for c in by_size.get(path.stat().st_size, []) if c[1] == fmt]
        if candidates:
            sha = compute_sha256(path)  # only files of a known size are read whole
            found = {book_id for book_id, _, known in candidates if known == sha}
            if found:
                return found
        if fmt != "epub":
            return set()
        with zipfile.ZipFile(path) as zf:
            meta, _cover = parse_opf(zf.read(find_opf_path(zf)))
    except Exception:  # unreadable or damaged files are simply not library books
        return set()
    names = {_norm(n) for creator in meta.authors for n in creator.split("&")}
    return {b for b, author in by_title.get(_norm(meta.title), []) if author in names}


def _epub_books_by_title(db: Database) -> dict[str, list[tuple[str, str]]]:
    """Title -> (book id, first author), for books with an EPUB (a PDF on the reader
    must not mark an EPUB-only book, nor the other way round)."""
    index: dict[str, list[tuple[str, str]]] = {}
    for book in db.get_books(BookFilter(), BookSort.TITLE_ASC):
        if any(f.format == "epub" for f in book.files):
            first_author = _norm(book.author.split("&")[0])
            index.setdefault(_norm(book.title), []).append((book.id, first_author))
    return index


def _norm(text: str) -> str:
    return " ".join(_INVISIBLE.sub("", text).casefold().split())


def _strictly_under(path: Path, folder: Path) -> bool:
    return path != folder and path.is_relative_to(folder)


def _remove_empty_folders(folder: Path, stop: Path) -> None:
    """Up to, never including, the books folder."""
    while _strictly_under(folder, stop):
        try:
            folder.rmdir()  # only succeeds when empty
        except OSError:
            return
        folder = folder.parent
