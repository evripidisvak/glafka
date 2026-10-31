use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use chrono::Utc;
use sha2::{Digest, Sha256};
use uuid::Uuid;
use walkdir::WalkDir;

use crate::db::Database;
use crate::error::{Error, Result};
use crate::metadata::calibre::parse_calibre_opf;
use crate::metadata::epub::parse_epub;
use crate::metadata::ExtractedMetadata;
use crate::models::{Book, BookFile};

#[derive(Debug, Default)]
pub struct ImportSummary {
    pub total_scanned: usize,
    pub books_imported: usize,
    pub files_attached: usize,
    pub duplicates_skipped: usize,
    pub errors: Vec<(PathBuf, String)>,
}

pub fn compute_sha256(path: &Path) -> Result<String> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 65536];

    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }

    Ok(format!("{:x}", hasher.finalize()))
}

pub fn sanitize_component(name: &str) -> String {
    let invalid = ['/', '\\', '?', '%', '*', ':', '|', '"', '<', '>', '.', ';', '='];
    let sanitized: String = name
        .chars()
        .map(|c| if invalid.contains(&c) || c.is_control() { '_' } else { c })
        .collect();
    let trimmed = sanitized.trim_matches(|c| c == ' ' || c == '_');
    if trimmed.is_empty() {
        "Unknown".to_string()
    } else {
        // Truncate to safe length
        trimmed.chars().take(80).collect()
    }
}

pub fn import_file(
    file_path: &Path,
    library_dir: &Path,
    db: &mut Database,
) -> Result<Option<Book>> {
    if !file_path.is_file() {
        return Err(Error::NotFound(format!("File '{}' not found", file_path.display())));
    }

    let ext = file_path
        .extension()
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();

    if !["epub", "pdf", "mobi", "azw3", "txt", "fb2"].contains(&ext.as_str()) {
        return Err(Error::Other(format!("Unsupported ebook format: .{}", ext)));
    }

    // 1. Content Hash & Duplicate Check
    let sha256 = compute_sha256(file_path)?;
    if let Some(existing_book) = db.find_by_sha256(&sha256)? {
        tracing::info!("File already exists in library for book '{}'", existing_book.title);
        return Ok(None);
    }

    // 2. Extract Metadata
    let meta = match ext.as_str() {
        "epub" => parse_epub(file_path).unwrap_or_else(|_| fallback_metadata(file_path)),
        _ => fallback_metadata(file_path),
    };

    let author_sanitized = sanitize_component(&meta.primary_author());
    let title_sanitized = sanitize_component(&meta.title);

    let book_dest_dir = library_dir.join(&author_sanitized).join(&title_sanitized);
    fs::create_dir_all(&book_dest_dir)?;

    let target_filename = format!("{}.{}", title_sanitized, ext);
    let target_file_path = book_dest_dir.join(&target_filename);

    // Copy book file into library
    fs::copy(file_path, &target_file_path)?;
    let file_size = fs::metadata(&target_file_path)?.len();

    // Save cover if extracted
    let mut cover_path = None;
    if let Some(ref cover) = meta.cover_image {
        let cover_ext = if cover.mime_type.contains("png") { "png" } else { "jpg" };
        let dest_cover = book_dest_dir.join(format!("cover.{}", cover_ext));
        if fs::write(&dest_cover, &cover.data).is_ok() {
            cover_path = Some(dest_cover.to_string_lossy().to_string());
        }
    }

    // 3. Check if Book already exists (attach format) or create new Book
    let primary_author = meta.primary_author();
    if let Some(existing_book) = db.find_by_title_author(&meta.title, &primary_author)? {
        let new_file = BookFile {
            id: Uuid::new_v4().to_string(),
            book_id: existing_book.id.clone(),
            format: ext,
            file_path: target_file_path.to_string_lossy().to_string(),
            file_size,
            sha256,
            created_at: Utc::now(),
        };
        db.add_file_to_book(&new_file)?;
        if existing_book.cover_path.is_none() && cover_path.is_some() {
            db.update_cover_path(&existing_book.id, cover_path.as_ref().unwrap())?;
        }
        return db.get_book_by_id(&existing_book.id);
    }

    let book_id = Uuid::new_v4().to_string();
    let now = Utc::now();

    let mut book = Book {
        id: book_id.clone(),
        title: meta.title,
        author: primary_author,
        series: meta.series,
        series_index: meta.series_index,
        description: meta.description,
        language: meta.language,
        cover_path,
        created_at: now,
        updated_at: now,
        files: vec![BookFile {
            id: Uuid::new_v4().to_string(),
            book_id: book_id.clone(),
            format: ext,
            file_path: target_file_path.to_string_lossy().to_string(),
            file_size,
            sha256,
            created_at: now,
        }],
        tags: meta.tags,
    };

    release_taken_series_index(db, &mut book)?;
    db.insert_book(&book)?;
    Ok(Some(book))
}

pub fn import_calibre_directory(
    calibre_book_dir: &Path,
    library_dir: &Path,
    db: &mut Database,
) -> Result<Option<Book>> {
    let opf_path = calibre_book_dir.join("metadata.opf");
    if !opf_path.exists() {
        return Ok(None);
    }

    let meta = parse_calibre_opf(&opf_path)?;
    let author_sanitized = sanitize_component(&meta.primary_author());
    let title_sanitized = sanitize_component(&meta.title);

    let book_dest_dir = library_dir.join(&author_sanitized).join(&title_sanitized);
    fs::create_dir_all(&book_dest_dir)?;

    // Save cover if available
    let mut cover_path = None;
    if let Some(ref cover) = meta.cover_image {
        let dest_cover = book_dest_dir.join("cover.jpg");
        if fs::write(&dest_cover, &cover.data).is_ok() {
            cover_path = Some(dest_cover.to_string_lossy().to_string());
        }
    }

    // Find all ebook formats in the directory
    let mut attached_files = Vec::new();
    let supported = ["epub", "pdf", "mobi", "azw3", "txt", "fb2"];

    for entry in fs::read_dir(calibre_book_dir)? {
        let entry = entry?;
        let path = entry.path();
        if path.is_file() {
            if let Some(ext) = path.extension().map(|s| s.to_string_lossy().to_lowercase()) {
                if supported.contains(&ext.as_str()) {
                    let sha256 = compute_sha256(&path)?;
                    if db.find_by_sha256(&sha256)?.is_some() {
                        continue;
                    }

                    let target_filename = format!("{}.{}", title_sanitized, ext);
                    let target_file_path = book_dest_dir.join(&target_filename);
                    fs::copy(&path, &target_file_path)?;
                    let file_size = fs::metadata(&target_file_path)?.len();

                    attached_files.push((ext, target_file_path.to_string_lossy().to_string(), file_size, sha256));
                }
            }
        }
    }

    if attached_files.is_empty() {
        return Ok(None);
    }

    // Check if book already exists
    let primary_author = meta.primary_author();
    let book_id = if let Some(existing) = db.find_by_title_author(&meta.title, &primary_author)? {
        for (format, path, size, sha) in attached_files {
            let new_file = BookFile {
                id: Uuid::new_v4().to_string(),
                book_id: existing.id.clone(),
                format,
                file_path: path,
                file_size: size,
                sha256: sha,
                created_at: Utc::now(),
            };
            db.add_file_to_book(&new_file)?;
        }
        if existing.cover_path.is_none() && cover_path.is_some() {
            db.update_cover_path(&existing.id, cover_path.as_ref().unwrap())?;
        }
        existing.id
    } else {
        let id = Uuid::new_v4().to_string();
        let now = Utc::now();
        let mut book = Book {
            id: id.clone(),
            title: meta.title,
            author: primary_author,
            series: meta.series,
            series_index: meta.series_index,
            description: meta.description,
            language: meta.language,
            cover_path,
            created_at: now,
            updated_at: now,
            files: attached_files
                .into_iter()
                .map(|(format, path, size, sha)| BookFile {
                    id: Uuid::new_v4().to_string(),
                    book_id: id.clone(),
                    format,
                    file_path: path,
                    file_size: size,
                    sha256: sha,
                    created_at: now,
                })
                .collect(),
            tags: meta.tags,
        };
        release_taken_series_index(db, &mut book)?;
        db.insert_book(&book)?;
        id
    };

    db.get_book_by_id(&book_id)
}

pub fn import_calibre_library<F: Fn(usize, usize, &str)>(
    calibre_root: &Path,
    library_dir: &Path,
    db: &mut Database,
    progress_cb: F,
) -> Result<ImportSummary> {
    let mut summary = ImportSummary::default();

    // 1. Collect all directories containing metadata.opf
    let mut calibre_book_dirs = Vec::new();
    for entry in WalkDir::new(calibre_root).min_depth(2).max_depth(3).into_iter().flatten() {
        if entry.file_type().is_file() && entry.file_name() == "metadata.opf" {
            if let Some(parent) = entry.path().parent() {
                calibre_book_dirs.push(parent.to_path_buf());
            }
        }
    }

    summary.total_scanned = calibre_book_dirs.len();

    // 2. Import each book folder
    for (i, dir) in calibre_book_dirs.iter().enumerate() {
        let dir_name = dir.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
        progress_cb(i + 1, summary.total_scanned, &dir_name);

        match import_calibre_directory(dir, library_dir, db) {
            Ok(Some(_book)) => {
                summary.books_imported += 1;
            }
            Ok(None) => {
                summary.duplicates_skipped += 1;
            }
            Err(e) => {
                summary.errors.push((dir.clone(), e.to_string()));
            }
        }
    }

    Ok(summary)
}

/// Slot taken: keep series, drop index. Unique index would otherwise fail the whole import.
fn release_taken_series_index(db: &Database, book: &mut Book) -> Result<()> {
    if let (Some(series), Some(idx)) = (&book.series, book.series_index) {
        if let Some(owner) = db.check_series_index_conflict(&book.id, series, idx)? {
            tracing::warn!(
                "'{}' already has index {} in series '{}'; importing '{}' without index",
                owner, idx, series, book.title
            );
            book.series_index = None;
        }
    }
    Ok(())
}

fn fallback_metadata(path: &Path) -> ExtractedMetadata {
    let filename = path
        .file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| "Unknown".to_string());

    // Check if filename is "Author - Title"
    if let Some((author, title)) = filename.split_once(" - ") {
        ExtractedMetadata {
            title: title.trim().to_string(),
            authors: vec![author.trim().to_string()],
            ..Default::default()
        }
    } else {
        ExtractedMetadata {
            title: filename,
            authors: vec!["Unknown Author".to_string()],
            ..Default::default()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use zip::write::SimpleFileOptions;

    fn create_dummy_epub(path: &Path, title: &str, author: &str, extra_meta: &str) {
        let file = fs::File::create(path).unwrap();
        let mut zip = zip::ZipWriter::new(file);

        let options = SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Stored);

        // mimetype
        zip.start_file("mimetype", options).unwrap();
        zip.write_all(b"application/epub+zip").unwrap();

        // META-INF/container.xml
        zip.start_file("META-INF/container.xml", options).unwrap();
        zip.write_all(br#"<?xml version="1.0"?>
        <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
          <rootfiles>
            <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
          </rootfiles>
        </container>"#).unwrap();

        // content.opf
        let opf = format!(
            r#"<?xml version="1.0"?>
            <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:title>{}</dc:title>
                <dc:creator>{}</dc:creator>
                {}
              </metadata>
              <manifest>
                <item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>
              </manifest>
            </package>"#,
            title, author, extra_meta
        );
        zip.start_file("content.opf", options).unwrap();
        zip.write_all(opf.as_bytes()).unwrap();

        zip.finish().unwrap();
    }

    #[test]
    fn test_import_and_duplicate_detection() {
        let test_dir = std::env::temp_dir().join(format!("grammata_test_{}", Uuid::new_v4()));
        fs::create_dir_all(&test_dir).unwrap();

        let library_dir = test_dir.join("library");
        let db_path = library_dir.join("test.db");
        fs::create_dir_all(&library_dir).unwrap();
        let mut db = Database::open(&db_path).unwrap();

        let source_epub = test_dir.join("source.epub");
        create_dummy_epub(&source_epub, "Hyperion", "Dan Simmons", "");

        // 1. Initial import
        let imported = import_file(&source_epub, &library_dir, &mut db).unwrap();
        assert!(imported.is_some());
        let book = imported.unwrap();
        assert_eq!(book.title, "Hyperion");
        assert_eq!(book.author, "Dan Simmons");
        assert_eq!(book.files.len(), 1);

        // 2. Duplicate import (same SHA256) should return None
        let duplicate = import_file(&source_epub, &library_dir, &mut db).unwrap();
        assert!(duplicate.is_none());

        // 3. Import same title/author in a different format (PDF)
        let source_pdf = test_dir.join("Dan Simmons - Hyperion.pdf");
        fs::write(&source_pdf, b"%PDF-1.4 dummy content").unwrap();

        // Should attach format to existing book
        let updated = import_file(&source_pdf, &library_dir, &mut db).unwrap();
        assert!(updated.is_some());
        let updated_book = updated.unwrap();
        assert_eq!(updated_book.id, book.id);
        assert_eq!(updated_book.files.len(), 2);

        // Cleanup
        let _ = fs::remove_dir_all(&test_dir);
    }

    #[test]
    fn test_import_keeps_book_when_series_slot_taken() {
        let test_dir = std::env::temp_dir().join(format!("grammata_test_{}", Uuid::new_v4()));
        let library_dir = test_dir.join("library");
        fs::create_dir_all(&library_dir).unwrap();
        let mut db = Database::open(&library_dir.join("test.db")).unwrap();

        let series_meta = r#"<meta name="calibre:series" content="Dublin Murder Squad"/>
                <meta name="calibre:series_index" content="5.0"/>"#;
        let first = test_dir.join("first.epub");
        let second = test_dir.join("second.epub");
        create_dummy_epub(&first, "The Secret Place", "Tana French", series_meta);
        create_dummy_epub(&second, "The Trespasser", "Tana French", series_meta);

        let a = import_file(&first, &library_dir, &mut db).unwrap().unwrap();
        assert_eq!(a.series_index, Some(5));

        let b = import_file(&second, &library_dir, &mut db).unwrap().unwrap();
        assert_eq!(b.series.as_deref(), Some("Dublin Murder Squad"));
        assert_eq!(b.series_index, None);
        let stored = db.get_book_by_id(&b.id).unwrap().unwrap();
        assert_eq!(stored.series_index, None);

        let _ = fs::remove_dir_all(&test_dir);
    }
}
