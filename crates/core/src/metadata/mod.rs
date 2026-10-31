pub mod calibre;
pub mod epub;

#[derive(Debug, Clone, Default)]
pub struct ExtractedMetadata {
    pub title: String,
    pub authors: Vec<String>,
    pub series: Option<String>,
    pub series_index: Option<i32>,
    pub description: Option<String>,
    pub language: Option<String>,
    pub tags: Vec<String>,
    pub cover_image: Option<CoverImage>,
}

#[derive(Debug, Clone)]
pub struct CoverImage {
    pub mime_type: String,
    pub data: Vec<u8>,
}

/// OPF stores floats ("1.0", "1.5"); "1.0".parse::<i32>() fails, so parse f64.
/// Fractional indices are truncated (1.5 -> 1).
pub(crate) fn parse_series_index(raw: &str) -> Option<i32> {
    let value: f64 = raw.trim().parse().ok()?;
    if !value.is_finite() || value < 0.0 {
        return None;
    }
    if value.fract() != 0.0 {
        tracing::warn!("Fractional series index {} truncated to {}", value, value.trunc());
    }
    Some(value.trunc() as i32)
}

impl ExtractedMetadata {
    pub fn primary_author(&self) -> String {
        if let Some(author) = self.authors.first() {
            let trimmed = author.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
        "Unknown Author".to_string()
    }
}
