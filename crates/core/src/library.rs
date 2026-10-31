use std::fs;
use std::path::{Path, PathBuf};

use crate::db::Database;
use crate::error::Result;
use crate::import::{import_calibre_library, import_file, ImportSummary};
use crate::models::{Book, BookFilter, BookSort};

pub struct LibraryManager {
    root_dir: PathBuf,
    db: Database,
}

impl LibraryManager {
    pub fn open<P: AsRef<Path>>(root_dir: P) -> Result<Self> {
        let root = root_dir.as_ref().to_path_buf();
        fs::create_dir_all(&root)?;

        // Store database and indexes in a hidden `.grammata/` folder to keep the library clean
        let hidden_dir = root.join(".grammata");
        fs::create_dir_all(&hidden_dir)?;

        let old_db = root.join("grammata.db");
        let new_db = hidden_dir.join("library.db");

        // Automatically migrate existing db files if present
        if old_db.exists() && !new_db.exists() {
            let _ = fs::rename(&old_db, &new_db);
            let _ = fs::rename(root.join("grammata.db-wal"), hidden_dir.join("library.db-wal"));
            let _ = fs::rename(root.join("grammata.db-shm"), hidden_dir.join("library.db-shm"));
        }

        let db = Database::open(&new_db)?;

        Ok(Self { root_dir: root, db })
    }

    pub fn default_path() -> PathBuf {
        if let Some(user_dirs) = std::env::var_os("HOME") {
            PathBuf::from(user_dirs).join("Books").join("Grammata")
        } else {
            PathBuf::from("Grammata")
        }
    }

    pub fn root_dir(&self) -> &Path {
        &self.root_dir
    }

    pub fn db(&self) -> &Database {
        &self.db
    }

    pub fn db_mut(&mut self) -> &mut Database {
        &mut self.db
    }

    pub fn import_file(&mut self, path: &Path) -> Result<Option<Book>> {
        import_file(path, &self.root_dir, &mut self.db)
    }

    pub fn import_calibre<F: Fn(usize, usize, &str)>(
        &mut self,
        calibre_root: &Path,
        progress_cb: F,
    ) -> Result<ImportSummary> {
        import_calibre_library(calibre_root, &self.root_dir, &mut self.db, progress_cb)
    }

    pub fn get_books(&self, filter: &BookFilter, sort: BookSort) -> Result<Vec<Book>> {
        self.db.get_books(filter, sort)
    }

    pub fn get_book_by_id(&self, id: &str) -> Result<Option<Book>> {
        self.db.get_book_by_id(id)
    }

    pub fn update_metadata(
        &mut self,
        id: &str,
        title: &str,
        author: &str,
        series: Option<&str>,
        series_index: Option<i32>,
        description: Option<&str>,
        tags: &[String],
    ) -> Result<()> {
        self.db.update_book_metadata(id, title, author, series, series_index, description, tags)
    }

    pub fn delete_book(&mut self, id: &str) -> Result<()> {
        // Also remove book files from disk if possible
        if let Ok(Some(book)) = self.db.get_book_by_id(id) {
            for file in &book.files {
                let _ = fs::remove_file(&file.file_path);
            }
            if let Some(ref cover) = book.cover_path {
                let _ = fs::remove_file(cover);
            }
        }
        self.db.delete_book(id)
    }

    pub fn get_authors(&self) -> Result<Vec<String>> {
        self.db.get_authors()
    }

    pub fn get_series(&self) -> Result<Vec<String>> {
        self.db.get_series()
    }

    pub fn get_tags(&self) -> Result<Vec<String>> {
        self.db.get_tags()
    }
}
