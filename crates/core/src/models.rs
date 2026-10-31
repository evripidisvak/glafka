use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Book {
    pub id: String,
    pub title: String,
    pub author: String,
    pub series: Option<String>,
    pub series_index: Option<i32>,
    pub description: Option<String>,
    pub language: Option<String>,
    pub cover_path: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub files: Vec<BookFile>,
    pub tags: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BookFile {
    pub id: String,
    pub book_id: String,
    pub format: String,
    pub file_path: String,
    pub file_size: u64,
    pub sha256: String,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SentRecord {
    pub id: String,
    pub book_id: String,
    pub target_type: String, // "usb" or "email"
    pub target_name: String,
    pub format: String,
    pub sent_at: DateTime<Utc>,
    pub status: String,
}

#[derive(Debug, Clone, Default)]
pub struct BookFilter {
    pub query: Option<String>,
    pub author: Option<String>,
    pub series: Option<String>,
    pub tag: Option<String>,
    pub only_sent: bool,
    pub recently_added: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BookSort {
    #[default]
    TitleAsc,
    TitleDesc,
    AuthorAsc,
    AuthorDesc,
    DateAddedDesc,
    DateAddedAsc,
    SeriesIndexAsc,
}
