import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from glafka.core.errors import GlafkaError, NotFoundError
from glafka.core.models import (
    Book,
    BookFile,
    BookFilter,
    BookSort,
    Destination,
    DeviceCopy,
    HistoryAction,
    HistoryEntry,
    utc_now,
)

# Schema as the Rust version created it; its databases report user_version 0.
_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    series TEXT,
    series_index INTEGER,
    description TEXT,
    language TEXT,
    cover_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS book_files (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    format TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS book_tags (
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (book_id, tag_id)
);

CREATE TABLE IF NOT EXISTS sent_history (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,
    target_name TEXT NOT NULL,
    format TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_books_author ON books(author);
CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
CREATE INDEX IF NOT EXISTS idx_books_series ON books(series);
CREATE INDEX IF NOT EXISTS idx_book_files_sha256 ON book_files(sha256);
CREATE INDEX IF NOT EXISTS idx_book_files_book_id ON book_files(book_id);
CREATE INDEX IF NOT EXISTS idx_sent_history_book_id ON sent_history(book_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_books_series_index
    ON books(series, series_index) WHERE series IS NOT NULL AND series_index IS NOT NULL;
"""


def _relative_to_library(stored: str, root: Path) -> str | None:
    """Library-relative form of a stored absolute path; None to leave the value as it is."""
    path = Path(stored)
    if not path.is_absolute():
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        pass
    # Library moved or copied since: longest tail (e.g. Author/Title/file) that exists here
    parts = path.parts[1:]
    for start in range(len(parts) - 1):  # tails of at least 2 parts; a bare filename is too vague
        tail = Path(*parts[start:])
        if (root / tail).exists():
            return tail.as_posix()
    return None


def _migrate_v2(conn: sqlite3.Connection, root: Path | None) -> None:
    """Paths relative to the library root, so the library can move; one file per format."""
    if root is not None:
        for table, column in (("book_files", "file_path"), ("books", "cover_path")):
            rows = conn.execute(
                f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL"
            ).fetchall()
            for rowid, stored in rows:
                relative = _relative_to_library(stored, root)
                if relative is not None:
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (relative, rowid)
                    )
    try:
        conn.execute(
            "CREATE UNIQUE INDEX idx_book_files_book_format ON book_files(book_id, format)"
        )
    except sqlite3.IntegrityError as e:
        raise GlafkaError(
            "Some books have two files in the same format; this library needs a manual fix"
        ) from e


# A migration is SQL, or a function given the connection and the library root (None if unknown).
Migration = str | Callable[[sqlite3.Connection, Path | None], None]

# MIGRATIONS[n] upgrades a database from version n to n + 1. Append only, never edit.
# v3: the hash of each file as imported. Writing edits into an EPUB changes `sha256`;
# `source_sha256` keeps recognising the original download as already in the library.
_SCHEMA_V3 = """
ALTER TABLE book_files ADD COLUMN source_sha256 TEXT;
UPDATE book_files SET source_sha256 = sha256;
CREATE INDEX idx_book_files_source_sha256 ON book_files(source_sha256);
"""

# v4: series names compare case-insensitively everywhere (the views already grouped that way);
# an empty name means no series.
_SCHEMA_V4 = """
UPDATE books SET series = NULL, series_index = NULL
    WHERE series IS NOT NULL AND TRIM(series) = '';
DROP INDEX IF EXISTS idx_books_series_index;
UPDATE books SET series_index = NULL WHERE id IN (
    SELECT later.id FROM books later JOIN books earlier
        ON earlier.series = later.series COLLATE NOCASE
        AND earlier.series_index = later.series_index
        AND (earlier.created_at < later.created_at
             OR (earlier.created_at = later.created_at AND earlier.id < later.id))
    WHERE later.series_index IS NOT NULL
);
CREATE UNIQUE INDEX idx_books_series_index ON books(series COLLATE NOCASE, series_index)
    WHERE series IS NOT NULL AND series_index IS NOT NULL;
"""

# v5: e-readers books are sent to
_SCHEMA_V5 = """
CREATE TABLE readers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    volume_uuid TEXT,
    books_folder TEXT NOT NULL,
    formats TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# v6: email readers (books_folder is "" for them)
_SCHEMA_V6 = "ALTER TABLE readers ADD COLUMN email TEXT;"

# v7: readers are called destinations (a USB drive or an email address)
_SCHEMA_V7 = "ALTER TABLE readers RENAME TO destinations;"

# v8: which books are on each USB reader (one row per copy), and what each send was
_SCHEMA_V8 = """
ALTER TABLE destinations ADD COLUMN last_checked TEXT;
ALTER TABLE sent_history ADD COLUMN destination_id TEXT;
ALTER TABLE sent_history ADD COLUMN sha256 TEXT;
ALTER TABLE sent_history ADD COLUMN file_size INTEGER;
CREATE TABLE on_device (
    destination_id TEXT NOT NULL REFERENCES destinations(id) ON DELETE CASCADE,
    book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    device_path TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (destination_id, book_id, device_path)
);
CREATE INDEX idx_on_device_book_id ON on_device(book_id);
"""

# v9: sent_history becomes history: more actions, and it outlives deleted books
_SCHEMA_V9 = """
CREATE TABLE history (
    id TEXT PRIMARY KEY,
    book_id TEXT REFERENCES books(id) ON DELETE SET NULL,
    book_title TEXT NOT NULL,
    book_author TEXT NOT NULL,
    action TEXT NOT NULL,
    destination_id TEXT,
    destination_kind TEXT NOT NULL,
    destination_name TEXT NOT NULL,
    format TEXT,
    at TEXT NOT NULL,
    sha256 TEXT,
    file_size INTEGER
);
INSERT INTO history SELECT sh.id, sh.book_id, COALESCE(b.title, ''), COALESCE(b.author, ''),
    sh.status, sh.destination_id, sh.target_type, sh.target_name, sh.format, sh.sent_at,
    sh.sha256, sh.file_size
    FROM sent_history sh LEFT JOIN books b ON b.id = sh.book_id;
DROP TABLE sent_history;
CREATE INDEX idx_history_book_id ON history(book_id);
CREATE INDEX idx_history_at ON history(at);
"""

MIGRATIONS: list[Migration] = [
    _SCHEMA_V1,
    _migrate_v2,
    _SCHEMA_V3,
    _SCHEMA_V4,
    _SCHEMA_V5,
    _SCHEMA_V6,
    _SCHEMA_V7,
    _SCHEMA_V8,
    _SCHEMA_V9,
]
SCHEMA_VERSION = len(MIGRATIONS)

_BOOK_COLUMNS = (
    "b.id, b.title, b.author, b.series, b.series_index, b.description, "
    "b.language, b.cover_path, b.created_at, b.updated_at"
)

_ORDER_BY = {
    BookSort.TITLE_ASC: " ORDER BY b.title COLLATE NOCASE ASC",
    BookSort.TITLE_DESC: " ORDER BY b.title COLLATE NOCASE DESC",
    BookSort.AUTHOR_ASC: " ORDER BY b.author COLLATE NOCASE ASC, b.title COLLATE NOCASE ASC",
    BookSort.AUTHOR_DESC: " ORDER BY b.author COLLATE NOCASE DESC, b.title COLLATE NOCASE ASC",
    BookSort.DATE_ADDED_DESC: " ORDER BY b.created_at DESC",
    BookSort.DATE_ADDED_ASC: " ORDER BY b.created_at ASC",
    BookSort.SERIES_INDEX_ASC: " ORDER BY b.series COLLATE NOCASE ASC, b.series_index ASC",
}


def clean_series(series: str | None, index: int | None) -> tuple[str | None, int | None]:
    """An empty series name means no series, and then no index either."""
    series = series.strip() if series else None
    return (series, index) if series else (None, None)


def _action(value: str) -> HistoryAction:
    try:
        return HistoryAction(value)
    except ValueError:
        return HistoryAction.SENT  # unknown in this version: shown as a send


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return utc_now()


class Database:
    """SQLite index of a library.

    With a `root`, file and cover paths are stored relative to it and returned absolute.
    """

    def __init__(self, conn: sqlite3.Connection, root: Path | None = None) -> None:
        self.conn = conn
        self.root = root.absolute() if root is not None else None
        conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    @classmethod
    def open(cls, path: Path | str, root: Path | None = None) -> "Database":
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode = WAL")
        return cls(conn, root)

    @classmethod
    def open_in_memory(cls) -> "Database":
        return cls(sqlite3.connect(":memory:"))

    def close(self) -> None:
        self.conn.close()

    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self) -> None:
        version = self.schema_version()
        if version > SCHEMA_VERSION:
            raise GlafkaError(
                f"Library database is version {version}; "
                f"this Glafka only knows up to version {SCHEMA_VERSION}"
            )
        for target in range(version + 1, SCHEMA_VERSION + 1):
            step = MIGRATIONS[target - 1]
            try:
                if isinstance(step, str):
                    # executescript commits any open transaction first, so BEGIN goes in the script
                    self.conn.executescript(
                        f"BEGIN; {step} PRAGMA user_version = {target}; COMMIT;"
                    )
                else:
                    self.conn.execute("BEGIN")
                    step(self.conn, self.root)
                    self.conn.execute(f"PRAGMA user_version = {target}")
                    self.conn.commit()
            except Exception:
                if self.conn.in_transaction:
                    self.conn.rollback()  # all or nothing: the version stays where it was
                raise

    # Paths: stored relative to the library root, handed out absolute

    def _to_db(self, path: str) -> str:
        if self.root is None:
            return path
        try:
            return Path(path).relative_to(self.root).as_posix()
        except ValueError:
            return path  # outside the library: keep it absolute

    def _from_db(self, stored: str) -> str:
        if self.root is None or Path(stored).is_absolute():
            return stored
        return str(self.root / stored)

    def _book_from_row(self, row: sqlite3.Row) -> Book:
        index = row[4]
        return Book(
            id=row[0],
            title=row[1],
            author=row[2],
            series=row[3],
            series_index=int(index) if index is not None else None,
            description=row[5],
            language=row[6],
            cover_path=self._from_db(row[7]) if row[7] is not None else None,
            created_at=_parse_time(row[8]),
            updated_at=_parse_time(row[9]),
        )

    # Queries

    def find_by_sha256(self, sha256: str) -> Book | None:
        row = self.conn.execute(
            "SELECT book_id FROM book_files WHERE sha256 = ?1 OR source_sha256 = ?1 LIMIT 1",
            (sha256,),
        ).fetchone()
        return self.get_book_by_id(row[0]) if row else None

    def find_by_title_author(self, title: str, author: str) -> Book | None:
        row = self.conn.execute(
            "SELECT id FROM books WHERE LOWER(title) = LOWER(?) AND LOWER(author) = LOWER(?)",
            (title.strip(), author.strip()),
        ).fetchone()
        return self.get_book_by_id(row[0]) if row else None

    def get_book_by_id(self, book_id: str) -> Book | None:
        row = self.conn.execute(
            f"SELECT {_BOOK_COLUMNS} FROM books b WHERE b.id = ?", (book_id,)
        ).fetchone()
        return self._with_details(self._book_from_row(row)) if row else None

    def get_files_for_book(self, book_id: str) -> list[BookFile]:
        rows = self.conn.execute(
            "SELECT id, book_id, format, file_path, file_size, sha256, created_at "
            "FROM book_files WHERE book_id = ? ORDER BY format ASC",
            (book_id,),
        )
        return [
            BookFile(
                id=r[0],
                book_id=r[1],
                format=r[2],
                file_path=self._from_db(r[3]),
                file_size=r[4],
                sha256=r[5],
                created_at=_parse_time(r[6]),
            )
            for r in rows
        ]

    def get_tags_for_book(self, book_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id = t.id "
            "WHERE bt.book_id = ? ORDER BY t.name ASC",
            (book_id,),
        )
        return [r[0] for r in rows]

    def get_books(self, filt: BookFilter, sort: BookSort) -> list[Book]:
        query = (
            f"SELECT DISTINCT {_BOOK_COLUMNS} FROM books b "
            "LEFT JOIN book_tags bt ON bt.book_id = b.id "
            "LEFT JOIN tags t ON t.id = bt.tag_id "
            "WHERE 1=1"
        )
        params: list[str] = []
        if filt.query and filt.query.strip():
            pattern = f"%{filt.query.strip()}%"
            query += " AND (b.title LIKE ? OR b.author LIKE ? OR b.series LIKE ?)"
            params += [pattern, pattern, pattern]
        if filt.author is not None:
            query += " AND b.author = ?"
            params.append(filt.author)
        if filt.series is not None:
            query += " AND b.series = ?"
            params.append(filt.series)
        if filt.tag is not None:
            query += " AND t.name = ? COLLATE NOCASE"
            params.append(filt.tag)
        if filt.on_reader:
            query += " AND EXISTS (SELECT 1 FROM on_device od WHERE od.book_id = b.id)"
        query += _ORDER_BY[sort]
        if filt.recently_added:
            query += " LIMIT 50"
        rows = self.conn.execute(query, params).fetchall()
        return [self._with_details(self._book_from_row(r)) for r in rows]

    def get_authors(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT author FROM books WHERE TRIM(author) != '' "
            "ORDER BY author COLLATE NOCASE ASC"
        )
        return [r[0] for r in rows]

    def get_series(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT series FROM books WHERE series IS NOT NULL AND TRIM(series) != '' "
            "ORDER BY series COLLATE NOCASE ASC"
        )
        return [r[0] for r in rows]

    def get_tags(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE ASC")
        return [r[0] for r in rows]

    def check_series_index_conflict(self, book_id: str, series: str, index: int) -> str | None:
        """Title of another book already holding this series slot, if any."""
        row = self.conn.execute(
            "SELECT title FROM books "
            "WHERE series = ? COLLATE NOCASE AND series_index = ? AND id != ?",
            (series, index, book_id),
        ).fetchone()
        return row[0] if row else None

    # Writes. `with self.conn` commits on success, rolls back on exception.

    def insert_book(self, book: Book) -> None:
        book.series, book.series_index = clean_series(book.series, book.series_index)
        with self.conn:
            self.conn.execute(
                "INSERT INTO books (id, title, author, series, series_index, description, "
                "language, cover_path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    book.id,
                    book.title,
                    book.author,
                    book.series,
                    book.series_index,
                    book.description,
                    book.language,
                    self._to_db(book.cover_path) if book.cover_path is not None else None,
                    book.created_at.isoformat(),
                    book.updated_at.isoformat(),
                ),
            )
            for file in book.files:
                self._insert_file(file, book.id)
            self._add_tags(book.id, book.tags)

    def add_file_to_book(self, file: BookFile) -> None:
        with self.conn:
            self._insert_file(file, file.book_id)

    def update_book_metadata(
        self,
        book_id: str,
        title: str,
        author: str,
        series: str | None,
        series_index: int | None,
        description: str | None,
        tags: list[str],
    ) -> None:
        series, series_index = clean_series(series, series_index)
        if series is not None and series_index is not None:
            conflict = self.check_series_index_conflict(book_id, series, series_index)
            if conflict is not None:
                raise GlafkaError(
                    f"'{conflict}' already has index {series_index} in series '{series}'"
                )
        with self.conn:
            self.conn.execute(
                "UPDATE books SET title = ?, author = ?, series = ?, series_index = ?, "
                "description = ?, updated_at = ? WHERE id = ?",
                (title, author, series, series_index, description, utc_now().isoformat(), book_id),
            )
            self.conn.execute("DELETE FROM book_tags WHERE book_id = ?", (book_id,))
            self._add_tags(book_id, tags)

    def update_file_content(self, file_id: str, sha256: str, file_size: int) -> None:
        """After the file was rewritten in place (e.g. metadata written into an EPUB)."""
        with self.conn:
            self.conn.execute(
                "UPDATE book_files SET sha256 = ?, file_size = ? WHERE id = ?",
                (sha256, file_size, file_id),
            )

    def move_book_files(
        self, book_id: str, file_paths: dict[str, str], cover_path: str | None
    ) -> None:
        """New locations after a rename: file id -> path, and the cover; one transaction."""
        with self.conn:
            for file_id, path in file_paths.items():
                self.conn.execute(
                    "UPDATE book_files SET file_path = ? WHERE id = ?", (self._to_db(path), file_id)
                )
            self.conn.execute(
                "UPDATE books SET cover_path = ? WHERE id = ?",
                (self._to_db(cover_path) if cover_path is not None else None, book_id),
            )

    def update_cover_path(self, book_id: str, cover_path: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE books SET cover_path = ?, updated_at = ? WHERE id = ?",
                (self._to_db(cover_path), utc_now().isoformat(), book_id),
            )

    def delete_book(self, book_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM books WHERE id = ?", (book_id,))

    # Destinations and what was sent to them

    def add_destination(self, destination: Destination) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO destinations "
                "(id, name, kind, volume_uuid, books_folder, formats, created_at, email) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    destination.id,
                    destination.name,
                    destination.kind,
                    destination.volume_uuid,
                    destination.books_folder,
                    ",".join(destination.formats),
                    destination.created_at.isoformat(),
                    destination.email,
                ),
            )

    def get_destinations(self) -> list[Destination]:
        rows = self.conn.execute(
            "SELECT id, name, kind, volume_uuid, books_folder, formats, created_at, email, "
            "last_checked FROM destinations ORDER BY name COLLATE NOCASE"
        )
        return [
            Destination(
                id=r[0],
                name=r[1],
                kind=r[2],
                volume_uuid=r[3],
                books_folder=r[4],
                formats=[f for f in r[5].split(",") if f],
                created_at=_parse_time(r[6]),
                email=r[7],
                last_checked=_parse_time(r[8]) if r[8] else None,
            )
            for r in rows
        ]

    def update_destination(self, destination: Destination) -> None:
        """Name, books folder, formats and address; kind and drive stay as added."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE destinations SET name = ?, books_folder = ?, formats = ?, email = ? "
                "WHERE id = ?",
                (
                    destination.name,
                    destination.books_folder,
                    ",".join(destination.formats),
                    destination.email,
                    destination.id,
                ),
            )
        if cur.rowcount == 0:
            raise NotFoundError(f"'{destination.name}' was removed")

    def delete_destination(self, destination_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM destinations WHERE id = ?", (destination_id,))

    def log(self, entry: HistoryEntry) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO history (id, book_id, book_title, book_author, action, "
                "destination_id, destination_kind, destination_name, format, at, sha256, "
                "file_size) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.id,
                    entry.book_id,
                    entry.book_title,
                    entry.book_author,
                    str(entry.action),
                    entry.destination_id,
                    entry.destination_kind,
                    entry.destination_name,
                    entry.format,
                    entry.at.isoformat(),
                    entry.sha256,
                    entry.file_size,
                ),
            )

    def history(self, book_id: str | None = None) -> list[HistoryEntry]:
        """Newest first; one book's, or everything."""
        query = (
            "SELECT id, book_id, book_title, book_author, action, destination_id, "
            "destination_kind, destination_name, format, at, sha256, file_size FROM history"
        )
        params: tuple[str, ...] = ()
        if book_id is not None:
            query += " WHERE book_id = ?"
            params = (book_id,)
        rows = self.conn.execute(query + " ORDER BY at DESC, rowid DESC", params)
        return [
            HistoryEntry(
                r[0],
                r[1],
                r[2],
                r[3],
                _action(r[4]),
                r[5],
                r[6],
                r[7],
                r[8],
                _parse_time(r[9]),
                r[10],
                r[11],
            )
            for r in rows
        ]

    # Books on USB readers

    def known_copies(self) -> list[tuple[str, str, str, int]]:
        """(book id, format, sha256, size) of every version a reader could hold: the
        library's current files and every copy that was sent."""
        rows = self.conn.execute(
            "SELECT book_id, format, sha256, file_size FROM book_files "
            "UNION SELECT book_id, format, sha256, file_size FROM history "
            "WHERE sha256 IS NOT NULL AND file_size IS NOT NULL "
            "AND book_id IN (SELECT id FROM books)"
        )
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    def replace_device_copies(
        self, destination_id: str, copies: list[DeviceCopy], checked_at: datetime
    ) -> None:
        """A scan's result becomes the whole truth for that reader."""
        with self.conn:
            self.conn.execute("DELETE FROM on_device WHERE destination_id = ?", (destination_id,))
            self.conn.executemany(
                "INSERT OR REPLACE INTO on_device (destination_id, book_id, device_path, seen_at) "
                "VALUES (?, ?, ?, ?)",
                [(c.destination_id, c.book_id, c.path, c.seen_at.isoformat()) for c in copies],
            )
            self.conn.execute(
                "UPDATE destinations SET last_checked = ? WHERE id = ?",
                (checked_at.isoformat(), destination_id),
            )

    def add_device_copy(self, copy: DeviceCopy) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO on_device (destination_id, book_id, device_path, seen_at) "
                "VALUES (?, ?, ?, ?)",
                (copy.destination_id, copy.book_id, copy.path, copy.seen_at.isoformat()),
            )

    def remove_device_copies(
        self, destination_id: str, book_id: str, paths: list[str] | None = None
    ) -> None:
        """The given copies, or all of that book on that reader."""
        with self.conn:
            if paths is None:
                self.conn.execute(
                    "DELETE FROM on_device WHERE destination_id = ? AND book_id = ?",
                    (destination_id, book_id),
                )
            else:
                self.conn.executemany(
                    "DELETE FROM on_device "
                    "WHERE destination_id = ? AND book_id = ? AND device_path = ?",
                    [(destination_id, book_id, p) for p in paths],
                )

    def device_copies(self, book_id: str) -> list[DeviceCopy]:
        rows = self.conn.execute(
            "SELECT destination_id, book_id, device_path, seen_at FROM on_device "
            "WHERE book_id = ? ORDER BY device_path",
            (book_id,),
        )
        return [DeviceCopy(r[0], r[1], r[2], _parse_time(r[3])) for r in rows]

    def books_on_readers(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT DISTINCT book_id FROM on_device")}

    # Helpers; callers own the transaction.

    def _with_details(self, book: Book) -> Book:
        book.files = self.get_files_for_book(book.id)
        book.tags = self.get_tags_for_book(book.id)
        return book

    def _insert_file(self, file: BookFile, book_id: str) -> None:
        self.conn.execute(
            "INSERT INTO book_files "
            "(id, book_id, format, file_path, file_size, sha256, source_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                file.id,
                book_id,
                file.format,
                self._to_db(file.file_path),
                file.file_size,
                file.sha256,
                file.sha256,  # as imported; stays when the file is rewritten
                file.created_at.isoformat(),
            ),
        )

    def _add_tags(self, book_id: str, tags: list[str]) -> None:
        for tag in tags:
            name = tag.strip()
            if not name:
                continue
            self.conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = self.conn.execute(
                "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()[0]
            self.conn.execute(
                "INSERT OR IGNORE INTO book_tags (book_id, tag_id) VALUES (?, ?)",
                (book_id, tag_id),
            )
