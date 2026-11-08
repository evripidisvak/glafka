import contextlib
import logging
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from glafka.core.config import LEGACY_NAME, default_library_path
from glafka.core.db import Database
from glafka.core.device import RemoveResult, ScanResult, remove_from_device, scan_device
from glafka.core.errors import RECOVERABLE_ERRORS, GlafkaError, MetadataError, NotFoundError
from glafka.core.history import record
from glafka.core.importer import (
    ImportPlan,
    ImportResult,
    ImportSummary,
    ItemOutcome,
    ProgressCallback,
    RemoveOriginal,
    claim_book_dir,
    compute_sha256,
    import_calibre_library,
    import_file,
    import_files,
    plan_import,
    run_import,
    sanitize_component,
)
from glafka.core.mail import send_by_email
from glafka.core.metadata import CoverImage, ExtractedMetadata
from glafka.core.metadata.epub import parse_epub
from glafka.core.metadata.epub_writer import write_epub_metadata
from glafka.core.metadata.pdf import parse_pdf
from glafka.core.models import (
    Book,
    BookFilter,
    BookSort,
    Destination,
    DeviceCopy,
    EmailAccount,
    HistoryAction,
    HistoryEntry,
)
from glafka.core.send import SendResult, send_to_usb

log = logging.getLogger(__name__)


@dataclass
class FileSync:
    """A book after its files were brought in line with its metadata, and what went wrong."""

    book: Book | None
    problems: list[str] = field(default_factory=list)


def _is_title_folder(folder_name: str, title_dir: str) -> bool:
    """'Title' or 'Title (2)': already named after this title."""
    return folder_name == title_dir or bool(
        re.fullmatch(rf"{re.escape(title_dir)} \(\d+\)", folder_name)
    )


class LibraryManager:
    """A library folder plus its index in the hidden <library>/.glafka/library.db."""

    def __init__(self, root_dir: Path, db: Database) -> None:
        self.root_dir = root_dir
        self.db = db

    @classmethod
    def open(cls, root_dir: Path | str) -> "LibraryManager":
        root = Path(root_dir).absolute()
        hidden_dir = root / ".glafka"
        legacy_dir = root / f".{LEGACY_NAME}"
        if legacy_dir.is_dir() and not hidden_dir.exists():
            legacy_dir.rename(hidden_dir)  # index from before the app was renamed
        hidden_dir.mkdir(parents=True, exist_ok=True)
        return cls(root, Database.open(hidden_dir / "library.db", root=root))

    @staticmethod
    def default_path() -> Path:
        return default_library_path()

    def close(self) -> None:
        self.db.close()

    def import_file(self, path: Path) -> ImportResult:
        return import_file(path, self.root_dir, self.db)

    def import_files(self, paths: Iterable[Path]) -> Iterator[ItemOutcome]:
        return import_files(paths, self.root_dir, self.db)

    def plan_import(self, paths: Iterable[Path]) -> ImportPlan:
        return plan_import(paths, self.root_dir)

    def run_import(
        self, plan: ImportPlan, remove_original: RemoveOriginal | None = None
    ) -> Iterator[ItemOutcome]:
        return run_import(plan, self.root_dir, self.db, remove_original)

    def import_calibre(self, calibre_root: Path, progress: ProgressCallback) -> ImportSummary:
        return import_calibre_library(calibre_root, self.root_dir, self.db, progress)

    def get_books(self, filt: BookFilter, sort: BookSort) -> list[Book]:
        return self.db.get_books(filt, sort)

    def get_book_by_id(self, book_id: str) -> Book | None:
        return self.db.get_book_by_id(book_id)

    def update_metadata(
        self,
        book_id: str,
        title: str,
        author: str,
        series: str | None,
        series_index: int | None,
        description: str | None,
        tags: list[str],
    ) -> None:
        self.db.update_book_metadata(
            book_id, title, author, series, series_index, description, tags
        )

    def sync_files(self, book_id: str) -> FileSync:
        """After a metadata save: write it into the book's EPUBs, rename its folder to match.

        Never raises for file trouble; the metadata is already saved, so problems are reported.
        """
        book = self.db.get_book_by_id(book_id)
        if book is None:
            return FileSync(None)  # deleted while this waited its turn
        problems = []
        for file in book.files:
            if file.format != "epub":
                continue
            path = Path(file.file_path)
            try:
                write_epub_metadata(path, book)
                self.db.update_file_content(file.id, compute_sha256(path), path.stat().st_size)
            except RECOVERABLE_ERRORS as e:
                log.warning("Could not write metadata into %s: %s", path, e)
                problems.append(f"{path.name} not updated ({e})")
        try:
            self._rename_to_match(book)
        except RECOVERABLE_ERRORS as e:
            log.warning("Could not rename the folder of %s: %s", book.title, e)
            problems.append(f"folder not renamed ({e})")
        return FileSync(self.db.get_book_by_id(book_id), problems)

    def reload_metadata(self, book_id: str) -> FileSync:
        """Fill the details in again from the book's file, then sync files like a save.

        What the file says wins; whatever it doesn't say keeps its current value.
        """
        book = self.db.get_book_by_id(book_id)
        if book is None:
            raise NotFoundError(f"Book '{book_id}' not found")
        file = next((f for fmt in _READABLE for f in book.files if f.format == fmt), None)
        if file is None:
            have = ", ".join(f.format.upper() for f in book.files) or "no files"
            raise GlafkaError(f"Glafka can only read details from EPUB and PDF files ({have})")
        if not Path(file.file_path).is_file():
            raise NotFoundError(f"{Path(file.file_path).name} is missing from the library folder")
        meta = _read_file_metadata(Path(file.file_path), file.format)

        series, series_index = book.series, book.series_index
        if meta.series:
            series, series_index = meta.series, meta.series_index
        if series and series_index is not None:
            if self.db.check_series_index_conflict(book.id, series, series_index) is not None:
                series_index = None  # slot taken: keep the series, like an import
        self.update_metadata(
            book.id,
            meta.title.strip() or book.title,
            meta.primary_author() if meta.authors else book.author,
            series,
            series_index,
            meta.description or book.description,
            meta.tags or book.tags,
        )
        if meta.cover_image is not None:
            self._replace_cover(book, Path(file.file_path).parent, meta.cover_image)
        return self.sync_files(book.id)

    def set_cover(self, book_id: str, image: CoverImage) -> FileSync:
        """Use the image as the cover: in the library, and inside the book's EPUBs.

        Only on this explicit action: a save never rewrites an EPUB's cover, even when the
        library cover differs (a Calibre import's cover.jpg often does).
        """
        book = self.db.get_book_by_id(book_id)
        if book is None:
            raise NotFoundError(f"Book '{book_id}' not found")
        if not book.files:
            raise GlafkaError(f"'{book.title}' has no files")
        self._replace_cover(book, Path(book.files[0].file_path).parent, image)
        book = self.db.get_book_by_id(book_id)
        problems = []
        for file in book.files:
            if file.format != "epub":
                continue  # PDFs: readers show their first page
            path = Path(file.file_path)
            try:
                write_epub_metadata(path, book, cover=image)
                self.db.update_file_content(file.id, compute_sha256(path), path.stat().st_size)
            except RECOVERABLE_ERRORS as e:
                log.warning("Could not put the cover into %s: %s", path, e)
                problems.append(f"{path.name} not updated ({e})")
        return FileSync(self.db.get_book_by_id(book_id), problems)

    def remove_cover(self, book_id: str) -> Book:
        """Back to the generated cover. Library only: the EPUB keeps its embedded image."""
        book = self.db.get_book_by_id(book_id)
        if book is None:
            raise NotFoundError(f"Book '{book_id}' not found")
        if book.cover_path:
            self.db.clear_cover_path(book_id)  # index first, like delete_book
            cover = Path(book.cover_path)
            if self._inside_library(cover):
                cover.unlink(missing_ok=True)
        return self.db.get_book_by_id(book_id)

    def _replace_cover(self, book: Book, book_dir: Path, image: CoverImage) -> None:
        """Atomic swap; an unchanged cover is left alone (its thumbnail stays valid)."""
        old = Path(book.cover_path) if book.cover_path else None
        if old is not None and old.is_file() and old.read_bytes() == image.data:
            return
        if not self._inside_library(book_dir):
            return
        new = book_dir / f"cover.{'png' if 'png' in image.mime_type else 'jpg'}"
        tmp = book_dir / f".cover-{os.getpid()}.part"
        try:
            tmp.write_bytes(image.data)
            os.replace(tmp, new)
        finally:
            tmp.unlink(missing_ok=True)
        self.db.update_cover_path(book.id, str(new))
        if old is not None and old != new and self._inside_library(old):
            old.unlink(missing_ok=True)  # e.g. cover.png replaced by cover.jpg

    def _rename_to_match(self, book: Book) -> None:
        """Move the book to Author/Title (or 'Title (n)') for its current title and author."""
        paths = [Path(f.file_path) for f in book.files]
        if not paths or not all(self._inside_library(p) for p in paths):
            return  # only ever move files the library owns
        old_dir = paths[0].parent
        author_dir = sanitize_component(book.author)
        title_dir = sanitize_component(book.title)
        if old_dir.parent == self.root_dir / author_dir and _is_title_folder(
            old_dir.name, title_dir
        ):
            return

        new_dir = claim_book_dir(self.root_dir / author_dir, title_dir)
        moved: list[tuple[Path, Path]] = []
        try:
            new_paths = {}
            for file, old in zip(book.files, paths, strict=True):
                new = new_dir / f"{title_dir}.{file.format}"
                old.rename(new)
                moved.append((old, new))
                new_paths[file.id] = str(new)
            cover = book.cover_path
            if cover and Path(cover).is_file() and self._inside_library(Path(cover)):
                new_cover = new_dir / Path(cover).name
                Path(cover).rename(new_cover)
                moved.append((Path(cover), new_cover))
                cover = str(new_cover)
            self.db.move_book_files(book.id, new_paths, cover)
        except BaseException:
            for old, new in reversed(moved):  # put everything back where the index says it is
                with contextlib.suppress(OSError):
                    new.rename(old)
            self._remove_empty_folders(new_dir)
            raise
        self._remove_empty_folders(old_dir)

    def delete_book(self, book_id: str) -> list[Path]:
        """Remove a book from the index, then its files, cover, and folders left empty.

        Index first, so a file that can't be removed never leaves a broken entry behind.
        Only paths inside the library are touched; other files in the folder are kept.
        Returns the files that could not be removed.
        """
        book = self.db.get_book_by_id(book_id)
        if book is None:
            raise NotFoundError(f"Book '{book_id}' not found")
        self.db.delete_book(book_id)

        paths = [Path(f.file_path) for f in book.files]
        if book.cover_path:
            paths.append(Path(book.cover_path))
        leftovers = []
        folders = set()
        for path in paths:
            if not self._inside_library(path):
                log.warning("Not deleting %s: outside the library", path)
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                log.warning("Could not delete %s: %s", path, e)
                leftovers.append(path)
            folders.add(path.parent)
        for folder in folders:
            self._remove_empty_folders(folder)
        return leftovers

    def _inside_library(self, path: Path) -> bool:
        # normpath: a stored "../" must not lead outside the library
        normalized = Path(os.path.normpath(path))
        return normalized != self.root_dir and normalized.is_relative_to(self.root_dir)

    def _remove_empty_folders(self, folder: Path) -> None:
        """Remove folder, then its parents, while empty; never the library root itself."""
        while self._inside_library(folder):
            try:
                folder.rmdir()  # only succeeds on an empty folder
            except OSError:
                return
            folder = folder.parent

    # Destinations

    def get_destinations(self) -> list[Destination]:
        return self.db.get_destinations()

    def add_destination(self, destination: Destination) -> None:
        self.db.add_destination(destination)

    def update_destination(self, destination: Destination) -> None:
        self.db.update_destination(destination)

    def delete_destination(self, destination_id: str) -> None:
        self.db.delete_destination(destination_id)

    def scan_device(self, destination: Destination, mount_root: Path) -> ScanResult:
        return scan_device(self.db, destination, mount_root)

    def remove_from_device(
        self, book_id: str, destination: Destination, mount_root: Path
    ) -> RemoveResult:
        return remove_from_device(self.db, book_id, destination, mount_root)

    def books_on_readers(self) -> set[str]:
        return self.db.books_on_readers()

    def device_copies(self, book_id: str) -> list[DeviceCopy]:
        return self.db.device_copies(book_id)

    def history(self, book_id: str | None = None) -> list[HistoryEntry]:
        """Newest first; one book's, or the whole library's."""
        return self.db.history(book_id)

    def forget_device_copies(self, destination: Destination, book_id: str) -> None:
        """Mark as Removed: off that reader until the next scan says otherwise."""
        self.db.remove_device_copies(destination.id, book_id)
        book = self.db.get_book_by_id(book_id)
        if book is not None:
            record(self.db, book, destination, HistoryAction.MARKED_REMOVED)

    def send_to_usb(
        self, book_id: str, destination: Destination, mount_root: Path, replace: bool = False
    ) -> SendResult:
        return send_to_usb(self.db, book_id, destination, mount_root, replace)

    def send_by_email(
        self, book_id: str, destination: Destination, account: EmailAccount, password: str
    ) -> SendResult:
        return send_by_email(self.db, book_id, destination, account, password)

    def get_authors(self) -> list[str]:
        return self.db.get_authors()

    def get_series(self) -> list[str]:
        return self.db.get_series()

    def get_tags(self) -> list[str]:
        return self.db.get_tags()


_READABLE = ("epub", "pdf")  # preferred source of details, in this order


def _read_file_metadata(path: Path, fmt: str) -> ExtractedMetadata:
    """Only what the file itself says (an import also guesses from the file name)."""
    if fmt == "pdf":
        return parse_pdf(path)
    try:
        return parse_epub(path)
    except Exception as e:  # zip, XML or missing-file errors from a damaged EPUB
        raise MetadataError(f"Could not read {path.name}: {e}") from e
