//! Grammata Core library
//!
//! Handles database management, metadata extraction, import logic,
//! converter backends, and device sync without any GTK dependencies.

pub mod config;
pub mod db;
pub mod error;
pub mod import;
pub mod library;
pub mod metadata;
pub mod models;

pub use config::AppConfig;
pub use db::Database;
pub use error::{Error, Result};
pub use library::LibraryManager;
pub use models::{Book, BookFile, BookFilter, BookSort, SentRecord};
