import sqlite3
from pathlib import Path

import pytest

from glafka.core.db import MIGRATIONS, SCHEMA_VERSION, Database
from glafka.core.errors import GlafkaError, NotFoundError
from glafka.core.models import Book, BookFile, BookFilter, BookSort, Destination, DeviceCopy


def test_db_roundtrip():
    db = Database.open_in_memory()
    book = Book(
        id="book-1",
        title="Dune",
        author="Frank Herbert",
        series="Dune Chronicles",
        series_index=1,
        description="Epic sci-fi novel",
        language="en",
        files=[
            BookFile(
                id="file-1",
                book_id="book-1",
                format="epub",
                file_path="/path/to/dune.epub",
                file_size=1024,
                sha256="abc123hash",
            )
        ],
        tags=["Sci-Fi", "Classic"],
    )
    db.insert_book(book)

    found = db.find_by_sha256("abc123hash")
    assert found is not None
    assert found.title == "Dune"
    assert found.author == "Frank Herbert"
    assert found.series_index == 1
    assert len(found.tags) == 2
    assert len(found.files) == 1

    assert db.get_authors() == ["Frank Herbert"]
    assert len(db.get_books(BookFilter(query="dune"), BookSort.TITLE_ASC)) == 1


def _series_book(book_id: str, series: str, index: int | None) -> Book:
    return Book(
        id=book_id, title=f"Title {book_id}", author="Author", series=series, series_index=index
    )


def test_series_index_unique_per_series():
    db = Database.open_in_memory()
    db.insert_book(_series_book("a", "Saga", 1))
    db.insert_book(_series_book("b", "Saga", 2))
    db.insert_book(_series_book("c", "Other", 1))  # same index, other series: allowed
    db.insert_book(_series_book("d", "Saga", None))  # no index: any number allowed
    db.insert_book(_series_book("e", "Saga", None))

    def save(book_id: str, index: int | None) -> None:
        db.update_book_metadata(book_id, "T", "A", "Saga", index, None, [])

    with pytest.raises(GlafkaError, match="Title a"):
        save("b", 1)  # slot taken: rejected, naming the owner
    save("b", 2)  # re-saving own slot is fine
    save("d", None)
    save("e", None)

    # DB index is the backstop if the pre-check is bypassed
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_book(_series_book("f", "Saga", 1))


def _rust_era_db(path: Path, files: list[tuple[str, str]]) -> None:
    """A database as the Rust version left it: schema v1, user_version 0, absolute paths.

    One book ("Dune", series "Saga" #1) with the given (format, absolute path) files.
    """
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 0")
    now = "2026-10-31T10:00:00+00:00"
    conn.execute(
        "INSERT INTO books (id, title, author, series, series_index, created_at, updated_at) "
        "VALUES ('a', 'Dune', 'Frank Herbert', 'Saga', 1, ?, ?)",
        (now, now),
    )
    for i, (fmt, file_path) in enumerate(files):
        conn.execute(
            "INSERT INTO book_files (id, book_id, format, file_path, file_size, sha256, "
            "created_at) VALUES (?, 'a', ?, ?, 4, ?, ?)",
            (f"f{i}", fmt, file_path, f"hash{i}", now),
        )
    conn.commit()
    conn.close()


def _stored_paths(db: Database) -> list[str]:
    return [r[0] for r in db.conn.execute("SELECT file_path FROM book_files ORDER BY id")]


def _book_file(root: Path, relative: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"book")
    return path


def test_rust_era_db_migrates_without_data_loss(tmp_path):
    root = tmp_path / "Books"
    epub = _book_file(root, "Frank Herbert/Dune/Dune.epub")
    _rust_era_db(tmp_path / "library.db", [("epub", str(epub))])

    db = Database.open(tmp_path / "library.db", root=root)

    assert db.schema_version() == SCHEMA_VERSION
    assert _stored_paths(db) == ["Frank Herbert/Dune/Dune.epub"]
    hashes = db.conn.execute("SELECT sha256, source_sha256 FROM book_files").fetchall()
    assert hashes == [("hash0", "hash0")]  # v3: the hash as imported, for duplicate checks
    assert db.find_by_sha256("hash0") is not None
    book = db.get_book_by_id("a")
    assert book is not None
    assert book.series_index == 1
    assert book.files[0].file_path == str(epub)


def test_migration_finds_files_of_a_moved_library(tmp_path):
    old = "/media/old-disk/Books/Frank Herbert/Dune/Dune.epub"  # gone
    root = tmp_path / "Books"
    epub = _book_file(root, "Frank Herbert/Dune/Dune.epub")
    _rust_era_db(tmp_path / "library.db", [("epub", old)])

    db = Database.open(tmp_path / "library.db", root=root)

    assert _stored_paths(db) == ["Frank Herbert/Dune/Dune.epub"]
    book = db.get_book_by_id("a")
    assert book is not None
    assert book.files[0].file_path == str(epub)


def test_failed_migration_changes_nothing(tmp_path):
    root = tmp_path / "Books"
    a = _book_file(root, "Frank Herbert/Dune/Dune.epub")
    b = _book_file(root, "Frank Herbert/Dune/Dune (copy).epub")
    _rust_era_db(tmp_path / "library.db", [("epub", str(a)), ("epub", str(b))])

    with pytest.raises(GlafkaError, match="two files in the same format"):
        Database.open(tmp_path / "library.db", root=root)

    conn = sqlite3.connect(tmp_path / "library.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1  # v1 done, v2 rolled back
    assert [r[0] for r in conn.execute("SELECT file_path FROM book_files ORDER BY id")] == [
        str(a),
        str(b),
    ]


def test_paths_are_stored_relative_and_returned_absolute(tmp_path):
    root = tmp_path / "Books"
    db = Database.open(tmp_path / "library.db", root=root)
    epub = str(root / "Frank Herbert/Dune/Dune.epub")
    cover = str(root / "Frank Herbert/Dune/cover.jpg")
    db.insert_book(
        Book(
            id="a",
            title="Dune",
            author="Frank Herbert",
            cover_path=cover,
            files=[BookFile("f", "a", "epub", epub, 4, "hash")],
        )
    )

    assert _stored_paths(db) == ["Frank Herbert/Dune/Dune.epub"]
    stored_cover = db.conn.execute("SELECT cover_path FROM books").fetchone()[0]
    assert stored_cover == "Frank Herbert/Dune/cover.jpg"
    book = db.get_book_by_id("a")
    assert book is not None
    assert (book.files[0].file_path, book.cover_path) == (epub, cover)


def test_book_cannot_have_two_files_in_one_format():
    db = Database.open_in_memory()
    db.insert_book(
        Book(id="a", title="Dune", author="F", files=[BookFile("f1", "a", "epub", "/x", 4, "h1")])
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.add_file_to_book(BookFile("f2", "a", "epub", "/y", 4, "h2"))


def test_empty_series_name_means_no_series():
    db = Database.open_in_memory()
    db.insert_book(_series_book("a", "  ", 1))
    db.insert_book(_series_book("b", "", 1))  # no clash: neither is in a series

    db.update_book_metadata("a", "T", "A", " ", 2, None, [])

    for book_id in ("a", "b"):
        book = db.get_book_by_id(book_id)
        assert book is not None
        assert (book.series, book.series_index) == (None, None)


def test_series_names_compare_case_insensitively():
    db = Database.open_in_memory()
    db.insert_book(_series_book("a", "Dublin Murder Squad", 5))
    db.insert_book(_series_book("b", "dublin murder squad", 6))

    with pytest.raises(GlafkaError, match="Title a"):
        db.update_book_metadata("b", "T", "A", "DUBLIN MURDER SQUAD", 5, None, [])
    with pytest.raises(sqlite3.IntegrityError):  # the database rule agrees
        db.insert_book(_series_book("c", "Dublin murder squad", 5))


def _db_at_version(path: Path, version: int) -> sqlite3.Connection:
    """A database as an older Glafka left it: only the first `version` migrations applied."""
    conn = sqlite3.connect(path)
    for step in MIGRATIONS[:version]:
        if isinstance(step, str):
            conn.executescript(step)
        else:
            step(conn, None)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    return conn


def test_v4_resolves_slots_taken_twice_by_case_variants(tmp_path):
    path = tmp_path / "library.db"
    conn = _db_at_version(path, 3)  # v3: series slots unique only with exact case
    for book_id, series, created in [
        ("a", "Saga", "2020"),
        ("b", "saga", "2021"),
        ("e", " ", "2022"),
    ]:
        conn.execute(
            "INSERT INTO books (id, title, author, series, series_index, created_at, updated_at) "
            "VALUES (?, ?, 'A', ?, 1, ?, ?)",
            (book_id, book_id, series, f"{created}-01-01T00:00:00+00:00", "2020-01-01"),
        )
    conn.commit()
    conn.close()

    db = Database.open(path)

    assert db.schema_version() == SCHEMA_VERSION
    rows = dict(
        (r[0], (r[1], r[2])) for r in db.conn.execute("SELECT id, series, series_index FROM books")
    )
    assert rows == {"a": ("Saga", 1), "b": ("saga", None), "e": (None, None)}  # earlier keeps #1


def test_v5_destinations_roundtrip():
    db = Database.open_in_memory()
    db.add_destination(
        Destination(id="r1", name="Era Lite", volume_uuid="1A2B-3C4D", formats=["epub", "pdf"])
    )

    [destination] = db.get_destinations()

    assert (destination.name, destination.volume_uuid, destination.books_folder) == (
        "Era Lite",
        "1A2B-3C4D",
        "Books",
    )
    assert destination.formats == ["epub", "pdf"]
    db.delete_destination("r1")
    assert db.get_destinations() == []


def test_v6_v7_keep_usb_and_store_email_destinations(tmp_path):
    path = tmp_path / "library.db"
    conn = _db_at_version(path, 5)
    conn.execute(
        "INSERT INTO readers VALUES ('u', 'Era Lite', 'usb', '1A2B-3C4D', 'Books', 'epub', '2026')"
    )
    conn.commit()
    conn.close()

    db = Database.open(path, tmp_path)
    db.add_destination(
        Destination(id="e", name="Cloud", kind="email", books_folder="", email="me@pbsync.com")
    )

    assert db.schema_version() == SCHEMA_VERSION
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "destinations" in tables and "readers" not in tables
    destinations = {r.id: (r.kind, r.volume_uuid, r.email) for r in db.get_destinations()}
    assert destinations == {"u": ("usb", "1A2B-3C4D", None), "e": ("email", None, "me@pbsync.com")}


def test_update_destination_keeps_kind_and_drive():
    db = Database.open_in_memory()
    db.add_destination(Destination(id="r1", name="PocketBook", volume_uuid="1A2B-3C4D"))

    db.update_destination(
        Destination(
            id="r1", name="Era Lite", kind="email", volume_uuid="other", books_folder="Ebooks"
        )
    )

    [destination] = db.get_destinations()
    assert (destination.name, destination.books_folder) == ("Era Lite", "Ebooks")
    assert (destination.kind, destination.volume_uuid) == ("usb", "1A2B-3C4D")
    with pytest.raises(NotFoundError):
        db.update_destination(Destination(id="gone", name="X"))


def test_v8_keeps_history_and_cascades_reader_contents(tmp_path):
    path = tmp_path / "library.db"
    conn = _db_at_version(path, 7)
    conn.executescript(
        "INSERT INTO books (id, title, author, created_at, updated_at) "
        "VALUES ('b', 'Hyperion', 'Dan Simmons', '2026', '2026');"
        "INSERT INTO destinations (id, name, kind, books_folder, formats, created_at) "
        "VALUES ('d', 'Era Lite', 'usb', 'Books', 'epub', '2026');"
        "INSERT INTO sent_history VALUES ('s', 'b', 'usb', 'PocketBook', 'epub', '2026', 'sent');"
    )
    conn.close()

    db = Database.open(path, tmp_path)

    [record] = db.sends_for_book("b")
    assert (record.target_name, record.destination_id, record.sha256) == ("PocketBook", None, None)
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    db.add_device_copy(DeviceCopy("d", "b", "Books/Simmons, Dan/Hyperion - Dan Simmons.epub"))
    assert db.books_on_readers() == {"b"}
    db.delete_destination("d")
    assert db.books_on_readers() == set()
