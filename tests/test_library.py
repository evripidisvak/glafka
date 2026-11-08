import sqlite3
from pathlib import Path

import pytest
from test_importer import make_epub

from glafka.core.errors import GlafkaError, NotFoundError
from glafka.core.importer import ImportPlan, ImportStatus, compute_sha256
from glafka.core.library import FileSync, LibraryManager
from glafka.core.metadata.epub import parse_epub
from glafka.core.models import Book, BookFile, BookFilter, BookSort


@pytest.fixture
def library(tmp_path):
    return LibraryManager.open(tmp_path / "Books")


def test_delete_removes_files_cover_and_empty_folders(tmp_path, library):
    epub = make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons", cover=b"cover")
    pdf = tmp_path / "Dan Simmons - Hyperion.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy content")
    book = library.import_file(epub).book
    library.import_file(pdf)

    leftovers = library.delete_book(book.id)

    assert leftovers == []
    assert library.get_book_by_id(book.id) is None
    assert not (library.root_dir / "Dan Simmons").exists()  # book and author folders emptied
    assert (library.root_dir / ".glafka" / "library.db").exists()
    assert epub.exists() and pdf.exists()  # the originals are never touched


def test_reimport_after_delete_gets_plain_folder(tmp_path, library):
    epub = make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons")
    library.delete_book(library.import_file(epub).book.id)

    again = library.import_file(epub)

    assert again.status is ImportStatus.IMPORTED  # its hash went with the deleted book
    assert Path(again.book.files[0].file_path).parent.name == "Hyperion"


def test_delete_keeps_other_files_in_the_folder(tmp_path, library):
    epub = make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons")
    book = library.import_file(epub).book
    book_dir = Path(book.files[0].file_path).parent
    (book_dir / "my notes.txt").write_text("keep me")

    library.delete_book(book.id)

    assert not Path(book.files[0].file_path).exists()
    assert (book_dir / "my notes.txt").read_text() == "keep me"


def test_delete_never_touches_files_outside_the_library(tmp_path, library):
    outside = tmp_path / "elsewhere" / "Dune.epub"
    outside.parent.mkdir()
    outside.write_bytes(b"not ours")
    library.db.insert_book(
        Book(
            id="a",
            title="Dune",
            author="Frank Herbert",
            files=[BookFile("f", "a", "epub", str(outside), 8, "hash")],
        )
    )

    library.delete_book("a")

    assert library.get_book_by_id("a") is None
    assert outside.read_bytes() == b"not ours"


def test_delete_unknown_book_raises(library):
    with pytest.raises(NotFoundError):
        library.delete_book("no-such-id")


# Bringing files in line with saved metadata


def save(library, book, **changes):
    fields = dict(
        title=book.title,
        author=book.author,
        series=book.series,
        series_index=book.series_index,
        description=book.description,
        tags=book.tags,
    )
    fields.update(changes)
    library.update_metadata(book.id, **fields)
    return library.sync_files(book.id)


def test_save_writes_epub_and_renames_folder(tmp_path, library):
    epub = make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons", cover=b"cover")
    book = library.import_file(epub).book
    old_dir = Path(book.files[0].file_path).parent

    sync = save(library, book, title="The Fall of Hyperion", series="Cantos", series_index=2)

    assert sync.problems == []
    moved = sync.book.files[0]
    assert Path(moved.file_path) == (
        library.root_dir / "Dan Simmons" / "The Fall of Hyperion" / "The Fall of Hyperion.epub"
    )
    assert not old_dir.exists()
    assert Path(sync.book.cover_path).read_bytes() == b"cover"
    on_disk = parse_epub(Path(moved.file_path))
    assert (on_disk.title, on_disk.series, on_disk.series_index) == (
        "The Fall of Hyperion",
        "Cantos",
        2,
    )
    assert moved.sha256 == compute_sha256(Path(moved.file_path))  # index follows the new content


def test_original_download_still_recognised_after_write_back(tmp_path, library):
    epub = make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons")
    book = library.import_file(epub).book
    save(library, book, description="Edited")
    removed = []

    [outcome] = library.run_import(ImportPlan(files=[epub]), removed.append)

    assert outcome.results[0].status is ImportStatus.ALREADY_IN_LIBRARY
    assert removed == [epub]  # with "move" on, the original download goes to the Trash


def test_no_rename_when_folder_already_matches(tmp_path, library):
    (library.root_dir / "Dan Simmons" / "Hyperion").mkdir(parents=True)  # taken
    book = library.import_file(make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons")).book
    path = Path(book.files[0].file_path)
    assert path.parent.name == "Hyperion (2)"

    sync = save(library, book, description="Edited")

    assert Path(sync.book.files[0].file_path) == path


def test_failed_rename_puts_everything_back(tmp_path, library, monkeypatch):
    book = library.import_file(make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons")).book
    path = Path(book.files[0].file_path)

    def db_fails(*_args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(library.db, "move_book_files", db_fails)
    sync = save(library, book, title="Renamed")

    assert any("folder not renamed" in p for p in sync.problems)
    assert path.is_file()  # moved back
    assert not (library.root_dir / "Dan Simmons" / "Renamed").exists()
    assert Path(sync.book.files[0].file_path) == path


def test_sync_of_a_deleted_book_is_quiet(tmp_path, library):
    book = library.import_file(make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons")).book
    library.delete_book(book.id)

    assert library.sync_files(book.id) == FileSync(None, [])


def test_library_index_moves_from_the_old_app_name(tmp_path):
    root = tmp_path / "Books"
    old_library = LibraryManager.open(root)
    old_library.import_file(make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons"))
    old_library.close()
    (root / ".glafka").rename(root / ".grammata")  # as the app left it before the rename

    library = LibraryManager.open(root)

    assert not (root / ".grammata").exists()
    assert (root / ".glafka" / "library.db").is_file()
    assert [b.title for b in library.db.get_books(BookFilter(), BookSort.TITLE_ASC)] == ["Hyperion"]


def test_reload_fills_in_a_pdf_imported_before_pdf_metadata(tmp_path, library, monkeypatch):
    from test_pdf import make_pdf

    from glafka.core.metadata import ExtractedMetadata

    pdf = make_pdf(tmp_path / "scan_0042.pdf", title="Hyperion", author="Dan Simmons")
    with monkeypatch.context() as m:  # as an import before PDF support: file name only
        m.setattr("glafka.core.importer.parse_pdf", lambda _path: ExtractedMetadata())
        book = library.import_file(pdf).book
    assert (book.title, book.author) == ("scan_0042", "Unknown Author")
    library.update_metadata(book.id, book.title, book.author, "Cantos", 1, "Mine", ["SF"])

    sync = library.reload_metadata(book.id)

    reloaded = sync.book
    assert (reloaded.title, reloaded.author) == ("Hyperion", "Dan Simmons")
    assert (reloaded.series, reloaded.series_index, reloaded.description, reloaded.tags) == (
        "Cantos",
        1,
        "Mine",
        ["SF"],
    )  # the PDF says nothing about these: kept
    assert Path(reloaded.files[0].file_path) == tmp_path / "Books/Dan Simmons/Hyperion/Hyperion.pdf"
    assert sync.problems == []


def test_reload_replaces_a_changed_cover_and_keeps_an_unchanged_one(tmp_path, library):
    epub = make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons", cover=b"first cover")
    book = library.import_file(epub).book
    cover = Path(book.cover_path)
    before = cover.stat().st_mtime_ns

    library.reload_metadata(book.id)
    assert cover.stat().st_mtime_ns == before  # same image: not rewritten

    stored = Path(book.files[0].file_path)
    make_epub(stored, "Hyperion", "Dan Simmons", cover=b"second cover")
    library.reload_metadata(book.id)

    reloaded = library.get_book_by_id(book.id)
    assert Path(reloaded.cover_path).read_bytes() == b"second cover"
    assert not list(Path(reloaded.cover_path).parent.glob("*.part"))


def test_reload_drops_a_series_index_another_book_holds(tmp_path, library):
    series = (
        '<meta name="calibre:series" content="Cantos"/>'
        '<meta name="calibre:series_index" content="1"/>'
    )
    first = library.import_file(make_epub(tmp_path / "a.epub", "Hyperion", "Dan Simmons", series))
    second = library.import_file(make_epub(tmp_path / "b.epub", "Endymion", "Dan Simmons")).book
    make_epub(Path(second.files[0].file_path), "Endymion", "Dan Simmons", series)

    reloaded = library.reload_metadata(second.id).book

    assert first.book.series_index == 1
    assert (reloaded.series, reloaded.series_index) == ("Cantos", None)


def test_reload_needs_an_epub_or_pdf(tmp_path, library):
    txt = tmp_path / "Dan Simmons - Hyperion.txt"
    txt.write_text("text")
    book = library.import_file(txt).book

    with pytest.raises(GlafkaError, match="EPUB and PDF"):
        library.reload_metadata(book.id)


def test_set_cover_replaces_the_library_cover_and_the_epub_cover(tmp_path, library):
    from glafka.core.metadata import CoverImage

    book = library.import_file(
        make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons", cover=b"old cover")
    ).book
    source_query = "SELECT source_sha256 FROM book_files WHERE id = ?"
    [(source,)] = library.db.conn.execute(source_query, (book.files[0].id,)).fetchall()

    sync = library.set_cover(book.id, CoverImage("image/jpeg", b"\xff\xd8 new cover"))

    updated = sync.book
    assert sync.problems == []
    assert Path(updated.cover_path).read_bytes() == b"\xff\xd8 new cover"
    epub = Path(updated.files[0].file_path)
    assert parse_epub(epub).cover_image.data == b"\xff\xd8 new cover"
    assert updated.files[0].sha256 == compute_sha256(epub)  # the index knows the new file
    [(still,)] = library.db.conn.execute(source_query, (book.files[0].id,)).fetchall()
    assert still == source  # the original download is still recognised


def test_set_cover_on_a_pdf_leaves_the_file_alone(tmp_path, library):
    from glafka.core.metadata import CoverImage

    pdf = tmp_path / "Dan Simmons - Hyperion.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy content")
    book = library.import_file(pdf).book
    before = book.files[0].sha256

    updated = library.set_cover(book.id, CoverImage("image/png", b"png cover")).book

    assert Path(updated.cover_path).name == "cover.png"
    assert updated.files[0].sha256 == before == compute_sha256(Path(updated.files[0].file_path))


def test_remove_cover_is_library_only(tmp_path, library):
    book = library.import_file(
        make_epub(tmp_path / "h.epub", "Hyperion", "Dan Simmons", cover=b"embedded")
    ).book
    cover = Path(book.cover_path)

    updated = library.remove_cover(book.id)

    assert updated.cover_path is None and not cover.exists()
    assert parse_epub(Path(updated.files[0].file_path)).cover_image.data == b"embedded"
