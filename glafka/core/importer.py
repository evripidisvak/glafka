import hashlib
import logging
import os
import shutil
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from glafka.core.db import Database, clean_series
from glafka.core.errors import RECOVERABLE_ERRORS, GlafkaError, NotFoundError
from glafka.core.metadata import ExtractedMetadata
from glafka.core.metadata.calibre import parse_calibre_opf
from glafka.core.metadata.epub import parse_epub
from glafka.core.metadata.pdf import parse_pdf
from glafka.core.models import Book, BookFile, utc_now

log = logging.getLogger(__name__)

SUPPORTED_FORMATS = ("epub", "pdf", "mobi", "azw3", "txt", "fb2")
_INVALID_CHARS = set('/\\?%*:|"<>.;=')

ProgressCallback = Callable[[int, int, str], None]
# Removes an original after import (the UI moves it to the Trash); raises if it can't
RemoveOriginal = Callable[[Path], None]


class ImportStatus(Enum):
    IMPORTED = auto()  # new book created
    FORMAT_ADDED = auto()  # file added as a new format of an existing book
    ALREADY_IN_LIBRARY = auto()  # identical file (same hash) already imported
    FORMAT_EXISTS = auto()  # book already has a file in this format; nothing written


@dataclass
class ImportResult:
    status: ImportStatus
    book: Book  # the new, updated or already existing book
    format: str


@dataclass
class ItemOutcome:
    """One item of a batch import (a file, or a Calibre book folder): its results, or the error."""

    path: Path
    results: list[ImportResult] = field(default_factory=list)
    error: str | None = None
    original_removed: bool = False
    removal_error: str | None = None  # the original was meant to be removed but stays


@dataclass
class ImportPlan:
    """What an import will process: Calibre book folders, then single files."""

    calibre_books: list[Path] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)

    @property
    def items(self) -> list[Path]:
        return [*self.calibre_books, *self.files]


@dataclass
class ImportSummary:
    """Totals for a Calibre library import; counts are per ebook file, not per book."""

    total_scanned: int = 0  # Calibre book folders found
    books_imported: int = 0
    formats_added: int = 0
    skipped: int = 0  # already in library, or the book already has that format
    errors: list[tuple[Path, str]] = field(default_factory=list)  # (book folder, message)


def compute_sha256(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def sanitize_component(name: str) -> str:
    """Safe single path component: invalid characters to '_', at most 80 characters."""
    cleaned = "".join(
        "_" if c in _INVALID_CHARS or unicodedata.category(c) == "Cc" else c for c in name
    )
    trimmed = cleaned.strip(" _")
    return trimmed[:80] if trimmed else "Unknown"


def _new_id() -> str:
    return str(uuid.uuid4())


def _extension(path: Path) -> str:
    return path.suffix.lower().removeprefix(".")


def copy_without_overwrite(src: Path, dest: Path) -> None:
    """Copy src to dest. Raises FileExistsError, leaving dest untouched, if dest exists."""
    fdst = open(dest, "xb")  # "x": create only; no race between an exists() check and the copy
    try:
        with open(src, "rb") as fsrc, fdst:
            shutil.copyfileobj(fsrc, fdst)
    except BaseException:
        dest.unlink(missing_ok=True)  # we created it; don't leave a partial copy
        raise


def import_file(file_path: Path, library_dir: Path, db: Database) -> ImportResult:
    """Copy one ebook into the library. The outcome is decided before anything is written."""
    if not file_path.is_file():
        raise NotFoundError(f"File '{file_path}' not found")

    ext = _extension(file_path)
    if ext not in SUPPORTED_FORMATS:
        raise GlafkaError(f"Unsupported ebook format: .{ext}")

    # 1. Identical file already imported?
    sha256 = compute_sha256(file_path)
    duplicate = db.find_by_sha256(sha256)
    if duplicate is not None:
        log.info("File already exists in library for book '%s'", duplicate.title)
        return ImportResult(ImportStatus.ALREADY_IN_LIBRARY, duplicate, ext)

    meta = _read_metadata(file_path, ext)
    return _place_new_file(file_path, ext, sha256, meta, library_dir, db)


def plan_import(paths: Iterable[Path], library_root: Path) -> ImportPlan:
    """Expand what the user picked or dropped into things to import.

    A folder holding a Calibre library (metadata.db) is imported book by book with Calibre's
    metadata; any other folder is searched for ebook files.
    """
    plan = ImportPlan()
    for path in paths:
        if path.is_dir():
            if is_calibre_library(path):
                plan.calibre_books += find_calibre_books(path)
            else:
                plan.files += collect_ebook_files(path, skip=library_root)
        elif path.is_file():
            plan.files.append(path)
    # The same file dropped on its own and inside a dropped folder: import it once
    plan.files = list(dict.fromkeys(plan.files))
    return plan


def collect_ebook_files(folder: Path, skip: Path | None = None) -> list[Path]:
    """Ebook files under `folder`, sorted; hidden entries and the `skip` folder are left out."""
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):  # doesn't follow symlinks: no loops
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and (skip is None or here / d != skip)
        )
        found += [
            here / name
            for name in sorted(filenames)
            if not name.startswith(".") and _extension(Path(name)) in SUPPORTED_FORMATS
        ]
    return found


def run_import(
    plan: ImportPlan,
    library_dir: Path,
    db: Database,
    remove_original: RemoveOriginal | None = None,
) -> Iterator[ItemOutcome]:
    """Import everything in the plan, one outcome per item, in `plan.items` order.

    With `remove_original`, single files are moved rather than copied; Calibre libraries
    are never changed.
    """
    yield from import_calibre_books(plan.calibre_books, library_dir, db)
    yield from import_files(plan.files, library_dir, db, remove_original)


# Outcomes after which the library holds the file's content, so the original may go.
# Not FORMAT_EXISTS: that file is a different edition and would be lost.
_ORIGINAL_SAFE_TO_REMOVE = {
    ImportStatus.IMPORTED,
    ImportStatus.FORMAT_ADDED,
    ImportStatus.ALREADY_IN_LIBRARY,
}


def import_files(
    paths: Iterable[Path],
    library_dir: Path,
    db: Database,
    remove_original: RemoveOriginal | None = None,
) -> Iterator[ItemOutcome]:
    """Import files one by one, yielding each outcome as soon as it is known.

    Stop early by not asking for more (or closing the iterator): it stops between files,
    and files already imported stay imported.
    """
    for path in paths:
        try:
            result = import_file(path, library_dir, db)
        except RECOVERABLE_ERRORS as e:
            log.warning("Import of %s failed: %s", path, e)
            yield ItemOutcome(path, error=str(e))
            continue
        outcome = ItemOutcome(path, [result])
        if remove_original is not None and result.status in _ORIGINAL_SAFE_TO_REMOVE:
            _remove_original(outcome, library_dir, remove_original)
        yield outcome


def _remove_original(outcome: ItemOutcome, library_dir: Path, remove: RemoveOriginal) -> None:
    path = outcome.path
    # A file picked from inside the library is the library's own copy, not an original
    if Path(os.path.realpath(path)).is_relative_to(os.path.realpath(library_dir)):
        return
    try:
        remove(path)
    except Exception as e:  # remover comes from the UI (GIO); any failure just keeps the file
        log.warning("Could not remove original %s: %s", path, e)
        outcome.removal_error = str(e)
    else:
        outcome.original_removed = True


def _place_new_file(
    file_path: Path,
    ext: str,
    sha256: str,
    meta: ExtractedMetadata,
    library_dir: Path,
    db: Database,
) -> ImportResult:
    """A file not in the library yet: new book, new format of a known book, or skip."""
    # 2. Same book (title + author) already there, maybe in this format?
    existing = db.find_by_title_author(meta.title, meta.primary_author())
    if existing is not None and any(f.format == ext for f in existing.files):
        log.info("'%s' already has a %s file; skipped %s", existing.title, ext, file_path)
        return ImportResult(ImportStatus.FORMAT_EXISTS, existing, ext)

    # 3. Only now touch the disk
    if existing is not None:
        return _add_format(existing, file_path, ext, sha256, meta, db)
    return _create_book(file_path, ext, sha256, meta, library_dir, db)


def claim_book_dir(author_dir: Path, title_dir: str) -> Path:
    """Create a new, empty book folder: 'Title', else 'Title (2)', 'Title (3)', ..."""
    author_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        candidate = author_dir / (title_dir if n == 1 else f"{title_dir} ({n})")
        try:
            candidate.mkdir()  # atomic claim: fails if the name is taken, even by a file
        except FileExistsError:
            n += 1
            continue
        return candidate


def _create_book(
    file_path: Path,
    ext: str,
    sha256: str,
    meta: ExtractedMetadata,
    library_dir: Path,
    db: Database,
) -> ImportResult:
    title_dir = sanitize_component(meta.title)
    book_dir = claim_book_dir(library_dir / sanitize_component(meta.primary_author()), title_dir)
    try:
        target = book_dir / f"{title_dir}.{ext}"
        copy_without_overwrite(file_path, target)
        book_id = _new_id()
        now = utc_now()
        book = Book(
            id=book_id,
            title=meta.title,
            author=meta.primary_author(),
            series=meta.series,
            series_index=meta.series_index,
            description=meta.description,
            language=meta.language,
            cover_path=_save_cover(meta, book_dir),
            created_at=now,
            updated_at=now,
            files=[
                BookFile(
                    id=_new_id(),
                    book_id=book_id,
                    format=ext,
                    file_path=str(target),
                    file_size=target.stat().st_size,
                    sha256=sha256,
                    created_at=now,
                )
            ],
            tags=meta.tags,
        )
        release_taken_series_index(db, book)
        db.insert_book(book)
    except BaseException:
        shutil.rmtree(book_dir, ignore_errors=True)  # freshly claimed, so only our files are in it
        raise
    return ImportResult(ImportStatus.IMPORTED, book, ext)


def _add_format(
    book: Book,
    file_path: Path,
    ext: str,
    sha256: str,
    meta: ExtractedMetadata,
    db: Database,
) -> ImportResult:
    """Store the new format next to the book's other files, wherever they live."""
    if not book.files:
        raise GlafkaError(f"'{book.title}' has no files to store the new format next to")
    sibling = Path(book.files[0].file_path)  # folder may be 'Title (2)'; never rebuild from title
    book_dir = sibling.parent
    book_dir.mkdir(parents=True, exist_ok=True)
    target = book_dir / f"{sibling.stem}.{ext}"
    copy_without_overwrite(file_path, target)
    try:
        db.add_file_to_book(
            BookFile(
                id=_new_id(),
                book_id=book.id,
                format=ext,
                file_path=str(target),
                file_size=target.stat().st_size,
                sha256=sha256,
            )
        )
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    if book.cover_path is None:
        cover_path = _save_cover(meta, book_dir)
        if cover_path is not None:
            db.update_cover_path(book.id, cover_path)
    updated = db.get_book_by_id(book.id)
    if updated is None:
        raise NotFoundError(f"Book '{book.id}' vanished during import")
    return ImportResult(ImportStatus.FORMAT_ADDED, updated, ext)


def import_calibre_directory(
    calibre_book_dir: Path, library_dir: Path, db: Database
) -> list[ImportResult]:
    """Import one Calibre book folder: metadata.opf, cover.jpg and each ebook file in it.

    Same rules as import_file; the metadata comes from metadata.opf, not from the files.
    """
    opf_path = calibre_book_dir / "metadata.opf"
    if not opf_path.exists():
        return []

    meta = parse_calibre_opf(opf_path)
    results = []
    for path in sorted(calibre_book_dir.iterdir()):
        ext = _extension(path)
        if not path.is_file() or ext not in SUPPORTED_FORMATS:
            continue
        sha256 = compute_sha256(path)
        duplicate = db.find_by_sha256(sha256)
        if duplicate is not None:
            results.append(ImportResult(ImportStatus.ALREADY_IN_LIBRARY, duplicate, ext))
            continue
        # First file creates the book; later ones find it by title + author and add formats
        results.append(_place_new_file(path, ext, sha256, meta, library_dir, db))
    return results


def is_calibre_library(folder: Path) -> bool:
    return (folder / "metadata.db").is_file()


def find_calibre_books(calibre_root: Path) -> list[Path]:
    """Book folders of a Calibre library: <root>/<Author>/<Title (id)>/metadata.opf."""
    opf_files = sorted(calibre_root.glob("*/metadata.opf")) + sorted(
        calibre_root.glob("*/*/metadata.opf")
    )
    return [opf.parent for opf in opf_files if opf.is_file()]


def import_calibre_books(
    book_dirs: Iterable[Path], library_dir: Path, db: Database
) -> Iterator[ItemOutcome]:
    """Like import_files, one outcome per Calibre book folder."""
    for book_dir in book_dirs:
        try:
            results = import_calibre_directory(book_dir, library_dir, db)
        except RECOVERABLE_ERRORS as e:
            log.warning("Import of %s failed: %s", book_dir, e)
            yield ItemOutcome(book_dir, error=str(e))
        else:
            yield ItemOutcome(book_dir, results)


def import_calibre_library(
    calibre_root: Path, library_dir: Path, db: Database, progress: ProgressCallback
) -> ImportSummary:
    """Import a whole Calibre library in one call; the UI uses plan_import + run_import."""
    summary = ImportSummary()
    book_dirs = find_calibre_books(calibre_root)
    summary.total_scanned = len(book_dirs)

    outcomes = import_calibre_books(book_dirs, library_dir, db)
    for i, outcome in enumerate(outcomes, start=1):
        progress(i, summary.total_scanned, outcome.path.name)
        if outcome.error is not None:
            summary.errors.append((outcome.path, outcome.error))
        for result in outcome.results:
            match result.status:
                case ImportStatus.IMPORTED:
                    summary.books_imported += 1
                case ImportStatus.FORMAT_ADDED:
                    summary.formats_added += 1
                case ImportStatus.ALREADY_IN_LIBRARY | ImportStatus.FORMAT_EXISTS:
                    summary.skipped += 1
    return summary


def release_taken_series_index(db: Database, book: Book) -> None:
    """Slot taken: keep series, drop index. The unique index would otherwise fail the import."""
    book.series, book.series_index = clean_series(book.series, book.series_index)
    if book.series is None or book.series_index is None:
        return
    owner = db.check_series_index_conflict(book.id, book.series, book.series_index)
    if owner is not None:
        log.warning(
            "'%s' already has index %d in series '%s'; importing '%s' without index",
            owner,
            book.series_index,
            book.series,
            book.title,
        )
        book.series_index = None


def _read_metadata(file_path: Path, ext: str) -> ExtractedMetadata:
    if ext == "epub":
        try:
            return parse_epub(file_path)
        except Exception as e:  # any unreadable EPUB still imports, with filename metadata
            log.warning("Could not read EPUB metadata from %s: %s", file_path, e)
    meta = fallback_metadata(file_path)
    if ext == "pdf":
        pdf = parse_pdf(file_path)  # what the PDF says wins; the file name fills the gaps
        meta.title = pdf.title or meta.title
        meta.authors = pdf.authors or meta.authors
        meta.cover_image = pdf.cover_image
    return meta


def _save_cover(meta: ExtractedMetadata, book_dir: Path) -> str | None:
    if meta.cover_image is None:
        return None
    cover_ext = "png" if "png" in meta.cover_image.mime_type else "jpg"
    dest = book_dir / f"cover.{cover_ext}"
    try:
        with open(dest, "xb") as f:  # never replace another book's cover
            f.write(meta.cover_image.data)
    except OSError:
        return None
    return str(dest)


def fallback_metadata(path: Path) -> ExtractedMetadata:
    """Metadata from the filename: "Author - Title.ext" or just "Title.ext"."""
    stem = path.stem or "Unknown"
    author, sep, title = stem.partition(" - ")
    if sep:
        return ExtractedMetadata(title=title.strip(), authors=[author.strip()])
    return ExtractedMetadata(title=stem, authors=["Unknown Author"])
