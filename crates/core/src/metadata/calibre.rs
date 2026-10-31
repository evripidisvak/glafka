use std::fs::File;
use std::io::BufReader;
use std::path::Path;
use quick_xml::events::Event;
use quick_xml::reader::Reader;

use crate::error::{Error, Result};
use crate::metadata::{parse_series_index, CoverImage, ExtractedMetadata};

pub fn parse_calibre_opf(opf_path: &Path) -> Result<ExtractedMetadata> {
    let file = File::open(opf_path)?;
    let mut reader = Reader::from_reader(BufReader::new(file));
    reader.config_mut().trim_text(true);

    let mut meta = ExtractedMetadata::default();
    let mut buf = Vec::new();

    let mut current_tag = String::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                current_tag = name.clone();

                if name == "meta" {
                    let mut meta_name = String::new();
                    let mut meta_content = String::new();

                    for attr in e.attributes().flatten() {
                        let key = String::from_utf8_lossy(attr.key.as_ref()).to_string();
                        let val = String::from_utf8_lossy(&attr.value).to_string();

                        if key == "name" {
                            meta_name = val;
                        } else if key == "content" {
                            meta_content = val;
                        }
                    }

                    if meta_name == "calibre:series" {
                        meta.series = Some(meta_content);
                    } else if meta_name == "calibre:series_index" {
                        if let Some(idx) = parse_series_index(&meta_content) {
                            meta.series_index = Some(idx);
                        }
                    }
                }
            }
            Ok(Event::Text(e)) => {
                let text = e.unescape().unwrap_or_default().to_string();
                if text.trim().is_empty() {
                    continue;
                }

                match current_tag.as_str() {
                    "dc:title" => {
                        if meta.title.is_empty() {
                            meta.title = text.trim().to_string();
                        }
                    }
                    "dc:creator" => {
                        meta.authors.push(text.trim().to_string());
                    }
                    "dc:description" => {
                        meta.description = Some(text.trim().to_string());
                    }
                    "dc:language" => {
                        if meta.language.is_none() {
                            meta.language = Some(text.trim().to_string());
                        }
                    }
                    "dc:subject" => {
                        let tag = text.trim().to_string();
                        if !tag.is_empty() && !meta.tags.contains(&tag) {
                            meta.tags.push(tag);
                        }
                    }
                    _ => {}
                }
            }
            Ok(Event::End(_)) => {
                current_tag.clear();
            }
            Ok(Event::Eof) => break,
            Err(e) => return Err(Error::Metadata(format!("XML error reading Calibre OPF: {}", e))),
            _ => {}
        }
        buf.clear();
    }

    if meta.title.is_empty() {
        if let Some(parent) = opf_path.parent() {
            meta.title = parent
                .file_name()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_else(|| "Unknown Title".to_string());
        } else {
            meta.title = "Unknown Title".to_string();
        }
    }

    // Check if cover.jpg exists right next to metadata.opf
    if let Some(parent) = opf_path.parent() {
        let cover_path = parent.join("cover.jpg");
        if cover_path.exists() {
            if let Ok(data) = std::fs::read(&cover_path) {
                meta.cover_image = Some(CoverImage {
                    mime_type: "image/jpeg".to_string(),
                    data,
                });
            }
        }
    }

    Ok(meta)
}
