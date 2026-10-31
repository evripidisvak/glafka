import sqlite3
import zipfile
from pathlib import Path

import pytest

from glafka.core.db import Database
from glafka.core.importer import (
    ImportPlan,
    ImportStatus,
    compute_sha256,
    copy_without_overwrite,
    import_calibre_library,
    import_file,
    import_files,
    plan_import,
    run_import,
)
from glafka.core.models import BookFilter, BookSort

OPF_TEMPLATE = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    {extra}
  </metadata>
  <manifest>
    <item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>
    {manifest_extra}
  </manifest>
</package>"""

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""


def make_epub(
    path: Path, title: str, author: str, extra: str = "", cover: bytes | None = None
) -> Path:
    manifest_extra = ""
    if cover is not None:
        extra += '<meta name="cover" content="cover-img"/>'
        manifest_extra = '<item id="cover-img" href="cover.jpg" media-type="image/jpeg"/>'
    opf = OPF_TEMPLATE.format(
        title=title, author=author, extra=extra, manifest_extra=manifest_extra
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("content.opf", opf)
        if cover is not None:
            zf.writestr("cover.jpg", cover)
    return path


@pytest.fixture
def library(tmp_path):
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    return library_dir, Database.open(library_dir / "test.db", root=library_dir)


def test_import_and_duplicate_detection(tmp_path, library):
    library_dir, db = library
    source_epub = make_epub(tmp_path / "source.epub", "Hyperion", "Dan Simmons")

    first = import_file(source_epub, library_dir, db)
    assert first.status is ImportStatus.IMPORTED
    book = first.book
    assert book.title == "Hyperion"
    assert book.author == "Dan Simmons"
    assert len(book.files) == 1
    stored = db.conn.execute("SELECT file_path FROM book_files").fetchone()[0]
    assert stored == "Dan Simmons/Hyperion/Hyperion.epub"  # relative to the library root

    # Same bytes again
    again = import_file(source_epub, library_dir, db)
    assert again.status is ImportStatus.ALREADY_IN_LIBRARY
    assert again.book.id == book.id

    # Same title/author in another format: added to the existing book
    source_pdf = tmp_path / "Dan Simmons - Hyperion.pdf"
    source_pdf.write_bytes(b"%PDF-1.4 dummy content")
    added = import_file(source_pdf, library_dir, db)
    assert added.status is ImportStatus.FORMAT_ADDED
    assert added.book.id == book.id
    assert len(added.book.files) == 2


def test_import_keeps_book_when_series_slot_taken(tmp_path, library):
    library_dir, db = library
    series_meta = """<meta name="calibre:series" content="Dublin Murder Squad"/>
        <meta name="calibre:series_index" content="5.0"/>"""
    first = make_epub(tmp_path / "first.epub", "The Secret Place", "Tana French", series_meta)
    second = make_epub(tmp_path / "second.epub", "The Trespasser", "Tana French", series_meta)

    a = import_file(first, library_dir, db).book
    assert a.series_index == 5

    b = import_file(second, library_dir, db).book
    assert b.series == "Dublin Murder Squad"
    assert b.series_index is None
    stored = db.get_book_by_id(b.id)
    assert stored is not None
    assert stored.series_index is None


def test_import_never_overwrites_existing_format(tmp_path, library):
    library_dir, db = library
    # Same title and author, different bytes and covers
    edition_a = make_epub(
        tmp_path / "a.epub",
        "Hyperion",
        "Dan Simmons",
        "<dc:description>A</dc:description>",
        cover=b"cover A",
    )
    edition_b = make_epub(
        tmp_path / "b.epub",
        "Hyperion",
        "Dan Simmons",
        "<dc:description>B</dc:description>",
        cover=b"cover B",
    )

    book = import_file(edition_a, library_dir, db).book
    result_b = import_file(edition_b, library_dir, db)

    assert result_b.status is ImportStatus.FORMAT_EXISTS
    assert result_b.book.id == book.id
    assert result_b.format == "epub"
    stored_path = Path(book.files[0].file_path)
    assert compute_sha256(stored_path) == compute_sha256(edition_a), "edition A was overwritten"
    assert book.cover_path is not None
    assert Path(book.cover_path).read_bytes() == b"cover A"
    stored = db.get_book_by_id(book.id)
    assert stored is not None
    assert len(stored.files) == 1


def test_format_added_brings_cover_when_book_has_none(tmp_path, library):
    library_dir, db = library
    pdf = tmp_path / "Dan Simmons - Hyperion.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy content")
    book = import_file(pdf, library_dir, db).book
    assert book.cover_path is None

    epub = make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons", cover=b"cover")
    result = import_file(epub, library_dir, db)

    assert result.status is ImportStatus.FORMAT_ADDED
    assert result.book.cover_path is not None
    assert Path(result.book.cover_path).read_bytes() == b"cover"


def test_new_book_gets_numbered_folder_when_name_taken(tmp_path, library):
    library_dir, db = library
    # Different books whose folder names collide: sanitize_component cuts at 80 characters
    one = make_epub(tmp_path / "one.epub", "x" * 80 + " One", "Author")
    two = make_epub(tmp_path / "two.epub", "x" * 80 + " Two", "Author")

    first = import_file(one, library_dir, db)
    second = import_file(two, library_dir, db)

    assert first.status is ImportStatus.IMPORTED
    assert second.status is ImportStatus.IMPORTED
    path_one = Path(first.book.files[0].file_path)
    path_two = Path(second.book.files[0].file_path)
    assert path_one.parent.name == "x" * 80
    assert path_two.parent.name == "x" * 80 + " (2)"
    assert compute_sha256(path_one) == compute_sha256(one)
    assert compute_sha256(path_two) == compute_sha256(two)


def test_format_added_goes_into_the_books_own_folder(tmp_path, library):
    library_dir, db = library
    author_dir = library_dir / "Dan Simmons"
    (author_dir / "Hyperion").mkdir(parents=True)  # name already taken, e.g. a leftover folder
    epub = make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons")
    book = import_file(epub, library_dir, db).book
    assert Path(book.files[0].file_path).parent.name == "Hyperion (2)"

    pdf = tmp_path / "Dan Simmons - Hyperion.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy content")
    result = import_file(pdf, library_dir, db)

    assert result.status is ImportStatus.FORMAT_ADDED
    pdf_file = next(f for f in result.book.files if f.format == "pdf")
    assert Path(pdf_file.file_path) == author_dir / "Hyperion (2)" / "Hyperion.pdf"
    assert not any((author_dir / "Hyperion").iterdir())


def test_failed_import_leaves_no_folder_behind(tmp_path, library, monkeypatch):
    library_dir, db = library
    epub = make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons")

    def fail(_book):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db, "insert_book", fail)
    with pytest.raises(sqlite3.OperationalError):
        import_file(epub, library_dir, db)
    assert not (library_dir / "Dan Simmons" / "Hyperion").exists()

    # So a retry gets the plain folder name, not 'Hyperion (2)'
    monkeypatch.undo()
    book = import_file(epub, library_dir, db).book
    assert Path(book.files[0].file_path).parent.name == "Hyperion"


def test_copy_without_overwrite_refuses_existing_target(tmp_path):
    src = tmp_path / "src.epub"
    src.write_bytes(b"new")
    dest = tmp_path / "dest.epub"
    dest.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        copy_without_overwrite(src, dest)
    assert dest.read_bytes() == b"old"

    fresh = tmp_path / "fresh.epub"
    copy_without_overwrite(src, fresh)
    assert fresh.read_bytes() == b"new"


# Calibre library import

CALIBRE_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">{author}</dc:creator>
  </metadata>
  <guide><reference type="cover" title="Cover" href="cover.jpg"/></guide>
</package>"""


def make_calibre_book(
    root: Path, author: str, title: str, files: dict[str, bytes], cover: bytes | None = None
) -> Path:
    """A book folder in Calibre's layout: <root>/<Author>/<Title (id)>/."""
    book_dir = root / author / f"{title} (1)"
    book_dir.mkdir(parents=True)
    (book_dir / "metadata.opf").write_text(CALIBRE_OPF.format(title=title, author=author))
    for ext, data in files.items():
        (book_dir / f"{title} - {author}.{ext}").write_bytes(data)
    if cover is not None:
        (book_dir / "cover.jpg").write_bytes(cover)
    return book_dir


def _no_progress(_done: int, _total: int, _name: str) -> None:
    pass


def test_calibre_import_creates_one_book_with_all_formats(tmp_path, library):
    library_dir, db = library
    calibre = tmp_path / "Calibre Library"
    make_calibre_book(
        calibre, "Dan Simmons", "Hyperion", {"epub": b"epub", "pdf": b"pdf"}, cover=b"cover"
    )
    progress: list[tuple[int, int, str]] = []

    summary = import_calibre_library(calibre, library_dir, db, lambda *a: progress.append(a))

    assert summary.total_scanned == 1
    assert (summary.books_imported, summary.formats_added, summary.skipped) == (1, 1, 0)
    assert summary.errors == []
    assert progress == [(1, 1, "Hyperion (1)")]
    book = db.find_by_title_author("Hyperion", "Dan Simmons")
    assert book is not None
    assert sorted(f.format for f in book.files) == ["epub", "pdf"]
    assert {Path(f.file_path).parent for f in book.files} == {
        library_dir / "Dan Simmons" / "Hyperion"
    }
    assert book.cover_path is not None
    assert Path(book.cover_path).read_bytes() == b"cover"


def test_calibre_import_never_overwrites_existing_files(tmp_path, library):
    library_dir, db = library
    epub_a = make_epub(tmp_path / "a.epub", "Hyperion", "Dan Simmons", cover=b"cover A")
    existing = import_file(epub_a, library_dir, db).book
    calibre = tmp_path / "Calibre Library"
    make_calibre_book(
        calibre, "Dan Simmons", "Hyperion", {"epub": b"other", "pdf": b"pdf"}, cover=b"cover C"
    )

    summary = import_calibre_library(calibre, library_dir, db, _no_progress)

    assert (summary.books_imported, summary.formats_added, summary.skipped) == (0, 1, 1)
    book = db.get_book_by_id(existing.id)
    assert book is not None
    assert sorted(f.format for f in book.files) == ["epub", "pdf"]
    assert compute_sha256(Path(existing.files[0].file_path)) == compute_sha256(epub_a)
    assert book.cover_path is not None
    assert Path(book.cover_path).read_bytes() == b"cover A"


def test_calibre_reimport_skips_everything(tmp_path, library):
    library_dir, db = library
    calibre = tmp_path / "Calibre Library"
    make_calibre_book(calibre, "Dan Simmons", "Hyperion", {"epub": b"epub", "pdf": b"pdf"})

    import_calibre_library(calibre, library_dir, db, _no_progress)
    again = import_calibre_library(calibre, library_dir, db, _no_progress)

    assert (again.books_imported, again.formats_added, again.skipped) == (0, 0, 2)


def test_calibre_import_reports_broken_folder_and_continues(tmp_path, library):
    library_dir, db = library
    calibre = tmp_path / "Calibre Library"
    broken = make_calibre_book(calibre, "Broken Author", "Broken", {"epub": b"x"})
    (broken / "metadata.opf").write_text("<package><metadata>not closed")
    make_calibre_book(calibre, "Dan Simmons", "Hyperion", {"epub": b"epub"})

    summary = import_calibre_library(calibre, library_dir, db, _no_progress)

    assert summary.books_imported == 1
    assert [folder for folder, _message in summary.errors] == [broken]


def test_import_files_streams_outcomes_and_stops_when_asked(tmp_path, library):
    library_dir, db = library
    sources = [make_epub(tmp_path / f"{i}.epub", f"Book {i}", "Author") for i in range(5)]
    bad = tmp_path / "notes.txt.bak"
    bad.write_text("not an ebook")

    outcomes = import_files([bad, *sources], library_dir, db)
    first = next(outcomes)
    second = next(outcomes)
    third = next(outcomes)
    outcomes.close()  # stop: the remaining files are never touched

    assert first.results == []
    assert first.error is not None
    assert "Unsupported" in first.error
    assert [r.status for r in second.results] == [ImportStatus.IMPORTED]
    assert [r.status for r in third.results] == [ImportStatus.IMPORTED]
    assert [b.title for b in db.get_books(BookFilter(), BookSort.TITLE_ASC)] == ["Book 0", "Book 1"]


# Planning an import from what the user picked or dropped


def test_plan_import_finds_ebooks_in_folders(tmp_path):
    library_root = tmp_path / "Downloads" / "My Library"  # library inside the picked folder
    downloads = tmp_path / "Downloads"
    make_epub_at = [downloads / "a.epub", downloads / "nested" / "deeper" / "b.pdf"]
    for path in make_epub_at:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    (downloads / "notes.txt.docx").write_bytes(b"not an ebook")
    (downloads / ".hidden").mkdir()
    (downloads / ".hidden" / "c.epub").write_bytes(b"x")
    (library_root / "Author" / "Title").mkdir(parents=True)
    (library_root / "Author" / "Title" / "Title.epub").write_bytes(b"x")
    single = tmp_path / "single.mobi"
    single.write_bytes(b"x")

    plan = plan_import([downloads, single], library_root)

    assert plan.calibre_books == []
    assert plan.files == [downloads / "a.epub", downloads / "nested" / "deeper" / "b.pdf", single]


def test_plan_import_detects_calibre_library(tmp_path):
    calibre = tmp_path / "Calibre Library"
    book = make_calibre_book(calibre, "Dan Simmons", "Hyperion", {"epub": b"epub"})
    (calibre / "metadata.db").write_bytes(b"")  # marks a Calibre library

    plan = plan_import([calibre], tmp_path / "Books")

    assert plan.calibre_books == [book]
    assert plan.files == []  # its ebooks come in through metadata.opf, not one by one


def test_run_import_does_calibre_books_then_files(tmp_path, library):
    library_dir, db = library
    calibre = tmp_path / "Calibre Library"
    book_dir = make_calibre_book(calibre, "Dan Simmons", "Hyperion", {"epub": b"e", "pdf": b"p"})
    loose = make_epub(tmp_path / "dune.epub", "Dune", "Frank Herbert")
    plan = ImportPlan(calibre_books=[book_dir], files=[loose])

    outcomes = list(run_import(plan, library_dir, db))

    assert [o.path for o in outcomes] == plan.items
    assert [r.status for r in outcomes[0].results] == [
        ImportStatus.IMPORTED,
        ImportStatus.FORMAT_ADDED,
    ]
    assert [r.status for r in outcomes[1].results] == [ImportStatus.IMPORTED]


# Moving instead of copying (originals removed after a successful import)


def test_originals_removed_only_when_the_library_holds_their_content(tmp_path, library):
    library_dir, db = library
    removed: list[Path] = []
    new = make_epub(tmp_path / "new.epub", "Dune", "Frank Herbert")
    duplicate = tmp_path / "dup.epub"
    duplicate.write_bytes(new.read_bytes())  # identical bytes
    other_edition = make_epub(
        tmp_path / "other.epub", "Dune", "Frank Herbert", "<dc:language>de</dc:language>"
    )
    broken = tmp_path / "notes.docx"
    broken.write_bytes(b"x")
    plan = ImportPlan(files=[new, duplicate, other_edition, broken])

    outcomes = list(run_import(plan, library_dir, db, removed.append))

    assert removed == [new, duplicate]  # imported, and identical duplicate
    assert [o.original_removed for o in outcomes] == [True, True, False, False]
    assert outcomes[2].results[0].status is ImportStatus.FORMAT_EXISTS  # other edition kept
    assert outcomes[3].error is not None  # failed import kept


def test_calibre_books_and_library_files_are_never_removed(tmp_path, library):
    library_dir, db = library
    removed: list[Path] = []
    book_dir = make_calibre_book(
        tmp_path / "Calibre Library", "Dan Simmons", "Hyperion", {"epub": b"e"}
    )
    in_library = import_file(make_epub(tmp_path / "d.epub", "Dune", "F"), library_dir, db).book
    library_copy = Path(in_library.files[0].file_path)
    plan = ImportPlan(calibre_books=[book_dir], files=[library_copy])

    outcomes = list(run_import(plan, library_dir, db, removed.append))

    assert removed == []
    assert outcomes[1].results[0].status is ImportStatus.ALREADY_IN_LIBRARY
    assert library_copy.exists()


def test_failed_removal_keeps_the_original_and_says_why(tmp_path, library):
    library_dir, db = library
    epub = make_epub(tmp_path / "d.epub", "Dune", "Frank Herbert")

    def no_trash(_path: Path) -> None:
        raise OSError("no trash on this drive")

    [outcome] = run_import(ImportPlan(files=[epub]), library_dir, db, no_trash)

    assert outcome.results[0].status is ImportStatus.IMPORTED
    assert not outcome.original_removed
    assert outcome.removal_error == "no trash on this drive"


def test_plan_lists_a_file_once_even_if_reached_twice(tmp_path):
    folder = tmp_path / "Dropped"
    folder.mkdir()
    epub = folder / "a.epub"
    epub.write_bytes(b"x")

    plan = plan_import([epub, folder], tmp_path / "Books")

    assert plan.files == [epub]
