use std::path::Path;
use chrono::{DateTime, Utc};
use rusqlite::{params, Connection, OptionalExtension};

use crate::error::Result;
use crate::models::{Book, BookFile, BookFilter, BookSort, SentRecord};

pub struct Database {
    conn: Connection,
}

impl Database {
    pub fn open(path: &Path) -> Result<Self> {
        let conn = Connection::open(path)?;
        let db = Self { conn };
        db.init()?;
        Ok(db)
    }

    #[cfg(test)]
    pub fn open_in_memory() -> Result<Self> {
        let conn = Connection::open_in_memory()?;
        let db = Self { conn };
        db.init()?;
        Ok(db)
    }

    fn init(&self) -> Result<()> {
        self.conn.execute_batch(
            "
            PRAGMA journal_mode = WAL;
            PRAGMA foreign_keys = ON;

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
            "
        )?;
        Ok(())
    }

    pub fn find_by_sha256(&self, sha256: &str) -> Result<Option<Book>> {
        let book_id: Option<String> = self.conn.query_row(
            "SELECT book_id FROM book_files WHERE sha256 = ?1",
            params![sha256],
            |row| row.get(0),
        ).optional()?;

        match book_id {
            Some(id) => self.get_book_by_id(&id),
            None => Ok(None),
        }
    }

    pub fn find_by_title_author(&self, title: &str, author: &str) -> Result<Option<Book>> {
        let book_id: Option<String> = self.conn.query_row(
            "SELECT id FROM books WHERE LOWER(title) = LOWER(?1) AND LOWER(author) = LOWER(?2)",
            params![title.trim(), author.trim()],
            |row| row.get(0),
        ).optional()?;

        match book_id {
            Some(id) => self.get_book_by_id(&id),
            None => Ok(None),
        }
    }

    pub fn get_book_by_id(&self, id: &str) -> Result<Option<Book>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, title, author, series, series_index, description, language, cover_path, created_at, updated_at
             FROM books WHERE id = ?1",
        )?;

        let mut book = match stmt.query_row(params![id], |row| {
            let created_str: String = row.get(8)?;
            let updated_str: String = row.get(9)?;
            Ok(Book {
                id: row.get(0)?,
                title: row.get(1)?,
                author: row.get(2)?,
                series: row.get(3)?,
                series_index: row.get(4)?,
                description: row.get(5)?,
                language: row.get(6)?,
                cover_path: row.get(7)?,
                created_at: DateTime::parse_from_rfc3339(&created_str)
                    .map(|dt| dt.with_timezone(&Utc))
                    .unwrap_or_else(|_| Utc::now()),
                updated_at: DateTime::parse_from_rfc3339(&updated_str)
                    .map(|dt| dt.with_timezone(&Utc))
                    .unwrap_or_else(|_| Utc::now()),
                files: Vec::new(),
                tags: Vec::new(),
            })
        }).optional()? {
            Some(b) => b,
            None => return Ok(None),
        };

        // Load files
        book.files = self.get_files_for_book(id)?;
        // Load tags
        book.tags = self.get_tags_for_book(id)?;

        Ok(Some(book))
    }

    pub fn get_files_for_book(&self, book_id: &str) -> Result<Vec<BookFile>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, book_id, format, file_path, file_size, sha256, created_at
             FROM book_files WHERE book_id = ?1 ORDER BY format ASC",
        )?;

        let rows = stmt.query_map(params![book_id], |row| {
            let created_str: String = row.get(6)?;
            Ok(BookFile {
                id: row.get(0)?,
                book_id: row.get(1)?,
                format: row.get(2)?,
                file_path: row.get(3)?,
                file_size: row.get(4)?,
                sha256: row.get(5)?,
                created_at: DateTime::parse_from_rfc3339(&created_str)
                    .map(|dt| dt.with_timezone(&Utc))
                    .unwrap_or_else(|_| Utc::now()),
            })
        })?;

        let mut files = Vec::new();
        for r in rows {
            files.push(r?);
        }
        Ok(files)
    }

    pub fn get_tags_for_book(&self, book_id: &str) -> Result<Vec<String>> {
        let mut stmt = self.conn.prepare(
            "SELECT t.name FROM tags t
             JOIN book_tags bt ON bt.tag_id = t.id
             WHERE bt.book_id = ?1 ORDER BY t.name ASC",
        )?;

        let rows = stmt.query_map(params![book_id], |row| row.get(0))?;
        let mut tags = Vec::new();
        for r in rows {
            tags.push(r?);
        }
        Ok(tags)
    }

    pub fn insert_book(&mut self, book: &Book) -> Result<()> {
        let tx = self.conn.transaction()?;

        tx.execute(
            "INSERT INTO books (id, title, author, series, series_index, description, language, cover_path, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                book.id,
                book.title,
                book.author,
                book.series,
                book.series_index,
                book.description,
                book.language,
                book.cover_path,
                book.created_at.to_rfc3339(),
                book.updated_at.to_rfc3339(),
            ],
        )?;

        for file in &book.files {
            tx.execute(
                "INSERT INTO book_files (id, book_id, format, file_path, file_size, sha256, created_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    file.id,
                    book.id,
                    file.format,
                    file.file_path,
                    file.file_size,
                    file.sha256,
                    file.created_at.to_rfc3339(),
                ],
            )?;
        }

        for tag in &book.tags {
            let tag_name = tag.trim();
            if tag_name.is_empty() {
                continue;
            }
            tx.execute(
                "INSERT OR IGNORE INTO tags (name) VALUES (?1)",
                params![tag_name],
            )?;
            let tag_id: i64 = tx.query_row(
                "SELECT id FROM tags WHERE name = ?1 COLLATE NOCASE",
                params![tag_name],
                |r| r.get(0),
            )?;
            tx.execute(
                "INSERT OR IGNORE INTO book_tags (book_id, tag_id) VALUES (?1, ?2)",
                params![book.id, tag_id],
            )?;
        }

        tx.commit()?;
        Ok(())
    }

    pub fn add_file_to_book(&self, file: &BookFile) -> Result<()> {
        self.conn.execute(
            "INSERT INTO book_files (id, book_id, format, file_path, file_size, sha256, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                file.id,
                file.book_id,
                file.format,
                file.file_path,
                file.file_size,
                file.sha256,
                file.created_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    /// Check if another book already occupies this series+index slot.
    /// Returns the title of the conflicting book, if any.
    pub fn check_series_index_conflict(
        &self,
        book_id: &str,
        series: &str,
        index: i32,
    ) -> Result<Option<String>> {
        let title: Option<String> = self.conn.query_row(
            "SELECT title FROM books WHERE series = ?1 AND series_index = ?2 AND id != ?3",
            params![series, index, book_id],
            |row| row.get(0),
        ).optional()?;
        Ok(title)
    }

    pub fn update_book_metadata(
        &mut self,
        id: &str,
        title: &str,
        author: &str,
        series: Option<&str>,
        series_index: Option<i32>,
        description: Option<&str>,
        tags: &[String],
    ) -> Result<()> {
        // Check for series index conflict before updating
        if let (Some(s), Some(idx)) = (series, series_index) {
            if !s.trim().is_empty() {
                if let Some(conflict_title) = self.check_series_index_conflict(id, s, idx)? {
                    return Err(crate::error::Error::Other(
                        format!("'{}' already has index {} in series '{}'", conflict_title, idx, s)
                    ));
                }
            }
        }

        let tx = self.conn.transaction()?;
        let now = Utc::now().to_rfc3339();

        tx.execute(
            "UPDATE books SET title = ?1, author = ?2, series = ?3, series_index = ?4, description = ?5, updated_at = ?6
             WHERE id = ?7",
            params![title, author, series, series_index, description, now, id],
        )?;

        // Replace tags
        tx.execute("DELETE FROM book_tags WHERE book_id = ?1", params![id])?;
        for tag in tags {
            let tag_name = tag.trim();
            if tag_name.is_empty() {
                continue;
            }
            tx.execute("INSERT OR IGNORE INTO tags (name) VALUES (?1)", params![tag_name])?;
            let tag_id: i64 = tx.query_row(
                "SELECT id FROM tags WHERE name = ?1 COLLATE NOCASE",
                params![tag_name],
                |r| r.get(0),
            )?;
            tx.execute(
                "INSERT OR IGNORE INTO book_tags (book_id, tag_id) VALUES (?1, ?2)",
                params![id, tag_id],
            )?;
        }

        tx.commit()?;
        Ok(())
    }

    pub fn update_cover_path(&self, id: &str, cover_path: &str) -> Result<()> {
        self.conn.execute(
            "UPDATE books SET cover_path = ?1, updated_at = ?2 WHERE id = ?3",
            params![cover_path, Utc::now().to_rfc3339(), id],
        )?;
        Ok(())
    }

    pub fn delete_book(&self, id: &str) -> Result<()> {
        self.conn.execute("DELETE FROM books WHERE id = ?1", params![id])?;
        Ok(())
    }

    pub fn get_books(&self, filter: &BookFilter, sort: BookSort) -> Result<Vec<Book>> {
        let mut query = String::from(
            "SELECT DISTINCT b.id, b.title, b.author, b.series, b.series_index, b.description,
                             b.language, b.cover_path, b.created_at, b.updated_at
             FROM books b
             LEFT JOIN book_tags bt ON bt.book_id = b.id
             LEFT JOIN tags t ON t.id = bt.tag_id
             LEFT JOIN sent_history sh ON sh.book_id = b.id
             WHERE 1=1"
        );

        let mut param_values: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();

        if let Some(ref q) = filter.query {
            let trimmed = q.trim();
            if !trimmed.is_empty() {
                let pattern = format!("%{}%", trimmed);
                query.push_str(" AND (b.title LIKE ? OR b.author LIKE ? OR b.series LIKE ?)");
                param_values.push(Box::new(pattern.clone()));
                param_values.push(Box::new(pattern.clone()));
                param_values.push(Box::new(pattern));
            }
        }

        if let Some(ref author) = filter.author {
            query.push_str(" AND b.author = ?");
            param_values.push(Box::new(author.clone()));
        }

        if let Some(ref series) = filter.series {
            query.push_str(" AND b.series = ?");
            param_values.push(Box::new(series.clone()));
        }

        if let Some(ref tag) = filter.tag {
            query.push_str(" AND t.name = ? COLLATE NOCASE");
            param_values.push(Box::new(tag.clone()));
        }

        if filter.only_sent {
            query.push_str(" AND sh.id IS NOT NULL");
        }

        match sort {
            BookSort::TitleAsc => query.push_str(" ORDER BY b.title COLLATE NOCASE ASC"),
            BookSort::TitleDesc => query.push_str(" ORDER BY b.title COLLATE NOCASE DESC"),
            BookSort::AuthorAsc => query.push_str(" ORDER BY b.author COLLATE NOCASE ASC, b.title COLLATE NOCASE ASC"),
            BookSort::AuthorDesc => query.push_str(" ORDER BY b.author COLLATE NOCASE DESC, b.title COLLATE NOCASE ASC"),
            BookSort::DateAddedDesc => query.push_str(" ORDER BY b.created_at DESC"),
            BookSort::DateAddedAsc => query.push_str(" ORDER BY b.created_at ASC"),
            BookSort::SeriesIndexAsc => query.push_str(" ORDER BY b.series COLLATE NOCASE ASC, b.series_index ASC"),
        }

        if filter.recently_added {
            query.push_str(" LIMIT 50");
        }

        let mut stmt = self.conn.prepare(&query)?;
        let params_slice: Vec<&dyn rusqlite::ToSql> = param_values.iter().map(|p| p.as_ref()).collect();

        let rows = stmt.query_map(params_slice.as_slice(), |row| {
            let created_str: String = row.get(8)?;
            let updated_str: String = row.get(9)?;
            Ok(Book {
                id: row.get(0)?,
                title: row.get(1)?,
                author: row.get(2)?,
                series: row.get(3)?,
                series_index: row.get(4)?,
                description: row.get(5)?,
                language: row.get(6)?,
                cover_path: row.get(7)?,
                created_at: DateTime::parse_from_rfc3339(&created_str)
                    .map(|dt| dt.with_timezone(&Utc))
                    .unwrap_or_else(|_| Utc::now()),
                updated_at: DateTime::parse_from_rfc3339(&updated_str)
                    .map(|dt| dt.with_timezone(&Utc))
                    .unwrap_or_else(|_| Utc::now()),
                files: Vec::new(),
                tags: Vec::new(),
            })
        })?;

        let mut books = Vec::new();
        for r in rows {
            let mut book = r?;
            book.files = self.get_files_for_book(&book.id)?;
            book.tags = self.get_tags_for_book(&book.id)?;
            books.push(book);
        }

        Ok(books)
    }

    pub fn get_authors(&self) -> Result<Vec<String>> {
        let mut stmt = self.conn.prepare(
            "SELECT DISTINCT author FROM books WHERE TRIM(author) != '' ORDER BY author COLLATE NOCASE ASC",
        )?;
        let rows = stmt.query_map([], |row| row.get(0))?;
        let mut list = Vec::new();
        for r in rows {
            list.push(r?);
        }
        Ok(list)
    }

    pub fn get_series(&self) -> Result<Vec<String>> {
        let mut stmt = self.conn.prepare(
            "SELECT DISTINCT series FROM books WHERE series IS NOT NULL AND TRIM(series) != '' ORDER BY series COLLATE NOCASE ASC",
        )?;
        let rows = stmt.query_map([], |row| row.get(0))?;
        let mut list = Vec::new();
        for r in rows {
            list.push(r?);
        }
        Ok(list)
    }

    pub fn get_tags(&self) -> Result<Vec<String>> {
        let mut stmt = self.conn.prepare(
            "SELECT name FROM tags ORDER BY name COLLATE NOCASE ASC",
        )?;
        let rows = stmt.query_map([], |row| row.get(0))?;
        let mut list = Vec::new();
        for r in rows {
            list.push(r?);
        }
        Ok(list)
    }

    pub fn log_sent(&self, record: &SentRecord) -> Result<()> {
        self.conn.execute(
            "INSERT INTO sent_history (id, book_id, target_type, target_name, format, sent_at, status)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                record.id,
                record.book_id,
                record.target_type,
                record.target_name,
                record.format,
                record.sent_at.to_rfc3339(),
                record.status,
            ],
        )?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_db_roundtrip() {
        let mut db = Database::open_in_memory().unwrap();

        let book = Book {
            id: "book-1".into(),
            title: "Dune".into(),
            author: "Frank Herbert".into(),
            series: Some("Dune Chronicles".into()),
            series_index: Some(1),
            description: Some("Epic sci-fi novel".into()),
            language: Some("en".into()),
            cover_path: None,
            created_at: Utc::now(),
            updated_at: Utc::now(),
            files: vec![BookFile {
                id: "file-1".into(),
                book_id: "book-1".into(),
                format: "epub".into(),
                file_path: "/path/to/dune.epub".into(),
                file_size: 1024,
                sha256: "abc123hash".into(),
                created_at: Utc::now(),
            }],
            tags: vec!["Sci-Fi".into(), "Classic".into()],
        };

        db.insert_book(&book).unwrap();

        // Check find by sha256
        let found = db.find_by_sha256("abc123hash").unwrap().expect("Book should exist");
        assert_eq!(found.title, "Dune");
        assert_eq!(found.author, "Frank Herbert");
        assert_eq!(found.series_index, Some(1));
        assert_eq!(found.tags.len(), 2);
        assert_eq!(found.files.len(), 1);

        // Check author list
        let authors = db.get_authors().unwrap();
        assert_eq!(authors, vec!["Frank Herbert"]);

        // Check filter
        let filter = BookFilter {
            query: Some("dune".into()),
            ..Default::default()
        };
        let search_results = db.get_books(&filter, BookSort::TitleAsc).unwrap();
        assert_eq!(search_results.len(), 1);
    }

    fn series_book(id: &str, series: &str, index: Option<i32>) -> Book {
        Book {
            id: id.into(),
            title: format!("Title {}", id),
            author: "Author".into(),
            series: Some(series.into()),
            series_index: index,
            description: None,
            language: None,
            cover_path: None,
            created_at: Utc::now(),
            updated_at: Utc::now(),
            files: Vec::new(),
            tags: Vec::new(),
        }
    }

    #[test]
    fn test_series_index_unique_per_series() {
        let mut db = Database::open_in_memory().unwrap();
        db.insert_book(&series_book("a", "Saga", Some(1))).unwrap();
        db.insert_book(&series_book("b", "Saga", Some(2))).unwrap();
        // Same index, other series: allowed
        db.insert_book(&series_book("c", "Other", Some(1))).unwrap();
        // No index: any number of books allowed
        db.insert_book(&series_book("d", "Saga", None)).unwrap();
        db.insert_book(&series_book("e", "Saga", None)).unwrap();

        let save = |db: &mut Database, id: &str, idx: Option<i32>| {
            db.update_book_metadata(id, "T", "A", Some("Saga"), idx, None, &[])
        };

        // Taking a used slot is rejected with the owner's title
        let err = save(&mut db, "b", Some(1)).unwrap_err();
        assert!(err.to_string().contains("Title a"), "{}", err);
        // Re-saving own slot is fine
        save(&mut db, "b", Some(2)).unwrap();
        // Index-less books save without conflict
        save(&mut db, "d", None).unwrap();
        save(&mut db, "e", None).unwrap();

        // DB index is the backstop if the pre-check is bypassed
        assert!(db.insert_book(&series_book("f", "Saga", Some(1))).is_err());
    }
}
