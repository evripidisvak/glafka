use std::fs::File;
use std::io::{BufReader, Read};
use std::path::Path;
use quick_xml::events::Event;
use quick_xml::reader::Reader;
use zip::ZipArchive;

use crate::error::{Error, Result};
use crate::metadata::{parse_series_index, CoverImage, ExtractedMetadata};

pub fn parse_epub(epub_path: &Path) -> Result<ExtractedMetadata> {
    let file = File::open(epub_path)?;
    let mut zip = ZipArchive::new(BufReader::new(file))
        .map_err(|e| Error::Metadata(format!("Invalid EPUB/ZIP archive: {}", e)))?;

    // 1. Locate the OPF package document
    let opf_path = find_opf_path(&mut zip)?;
    let opf_dir = Path::new(&opf_path).parent().unwrap_or(Path::new("")).to_path_buf();

    // 2. Read the OPF file
    let mut opf_xml = String::new();
    {
        let mut opf_file = zip.by_name(&opf_path)
            .map_err(|e| Error::Metadata(format!("Could not read OPF '{}': {}", opf_path, e)))?;
        opf_file.read_to_string(&mut opf_xml)?;
    }

    // 3. Parse OPF metadata and manifest
    let (mut meta, cover_item_href) = parse_opf_xml(&opf_xml)?;

    // 4. Fallback title if empty
    if meta.title.is_empty() {
        meta.title = epub_path
            .file_stem()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_else(|| "Unknown Title".to_string());
    }

    // 5. Extract cover image if found
    if let Some(href) = cover_item_href {
        let zip_cover_path = normalize_zip_path(&opf_dir, &href);
        if let Ok(mut cover_entry) = zip.by_name(&zip_cover_path) {
            let mut data = Vec::new();
            if cover_entry.read_to_end(&mut data).is_ok() {
                let mime = if zip_cover_path.ends_with(".png") {
                    "image/png".to_string()
                } else {
                    "image/jpeg".to_string()
                };
                meta.cover_image = Some(CoverImage {
                    mime_type: mime,
                    data,
                });
            }
        }
    }

    Ok(meta)
}

fn find_opf_path<R: Read + std::io::Seek>(zip: &mut ZipArchive<R>) -> Result<String> {
    // Try META-INF/container.xml
    if let Ok(mut container_file) = zip.by_name("META-INF/container.xml") {
        let mut content = String::new();
        if container_file.read_to_string(&mut content).is_ok() {
            if let Some(path) = parse_container_xml(&content) {
                return Ok(path);
            }
        }
    }

    // Fallbacks
    for name in &["OEBPS/content.opf", "content.opf", "OPS/package.opf"] {
        if zip.by_name(name).is_ok() {
            return Ok(name.to_string());
        }
    }

    // Scan zip entries for any .opf
    for i in 0..zip.len() {
        if let Ok(file) = zip.by_index(i) {
            if file.name().ends_with(".opf") {
                return Ok(file.name().to_string());
            }
        }
    }

    Err(Error::Metadata("Could not locate OPF package document in EPUB".into()))
}

fn parse_container_xml(xml: &str) -> Option<String> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) | Ok(Event::Empty(e)) => {
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                if name.ends_with("rootfile") {
                    for attr in e.attributes().flatten() {
                        let key = String::from_utf8_lossy(attr.key.as_ref()).to_string();
                        if key == "full-path" {
                            return Some(String::from_utf8_lossy(&attr.value).to_string());
                        }
                    }
                }
            }
            Ok(Event::Eof) | Err(_) => break,
            _ => {}
        }
        buf.clear();
    }
    None
}

fn parse_opf_xml(xml: &str) -> Result<(ExtractedMetadata, Option<String>)> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);
    let mut buf = Vec::new();

    let mut meta = ExtractedMetadata::default();
    let mut current_tag = String::new();

    let mut cover_item_id: Option<String> = None;
    let mut manifest_items: Vec<(String, String, String, String)> = Vec::new(); // (id, href, media_type, properties)
    let mut epbu3_collection_id: Option<String> = None;

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                current_tag = name.clone();

                if name == "meta" {
                    let mut meta_name = String::new();
                    let mut meta_content = String::new();
                    let mut meta_property = String::new();
                    let mut meta_id = String::new();
                    let mut meta_refines = String::new();

                    for attr in e.attributes().flatten() {
                        let key = String::from_utf8_lossy(attr.key.as_ref()).to_string();
                        let val = String::from_utf8_lossy(&attr.value).to_string();

                        match key.as_str() {
                            "name" => meta_name = val,
                            "content" => meta_content = val,
                            "property" => meta_property = val,
                            "id" => meta_id = val,
                            "refines" => meta_refines = val,
                            _ => {}
                        }
                    }

                    if meta_name == "calibre:series" {
                        meta.series = Some(meta_content.clone());
                    } else if meta_name == "calibre:series_index" {
                        if let Some(idx) = parse_series_index(&meta_content) {
                            meta.series_index = Some(idx);
                        }
                    } else if meta_name == "cover" && !meta_content.is_empty() {
                        cover_item_id = Some(meta_content.clone());
                    }

                    if meta_property == "belongs-to-collection" {
                        epbu3_collection_id = Some(meta_id);
                    } else if !meta_refines.is_empty() && meta_property == "group-position" {
                        if let Some(idx) = parse_series_index(&meta_content) {
                            meta.series_index = Some(idx);
                        }
                    }
                }
            }
            Ok(Event::Empty(e)) => {
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                if name == "item" {
                    let mut id = String::new();
                    let mut href = String::new();
                    let mut media_type = String::new();
                    let mut properties = String::new();

                    for attr in e.attributes().flatten() {
                        let key = String::from_utf8_lossy(attr.key.as_ref()).to_string();
                        let val = String::from_utf8_lossy(&attr.value).to_string();

                        match key.as_str() {
                            "id" => id = val,
                            "href" => href = val,
                            "media-type" => media_type = val,
                            "properties" => properties = val,
                            _ => {}
                        }
                    }

                    manifest_items.push((id, href, media_type, properties));
                } else if name == "meta" {
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
                        meta.series = Some(meta_content.clone());
                    } else if meta_name == "calibre:series_index" {
                        if let Some(idx) = parse_series_index(&meta_content) {
                            meta.series_index = Some(idx);
                        }
                    } else if meta_name == "cover" && !meta_content.is_empty() {
                        cover_item_id = Some(meta_content.clone());
                    }
                }
            }
            Ok(Event::Text(e)) => {
                let text = e.unescape().unwrap_or_default().to_string();
                let trimmed = text.trim();
                if trimmed.is_empty() {
                    continue;
                }

                if current_tag.ends_with("title") && meta.title.is_empty() {
                    meta.title = trimmed.to_string();
                } else if current_tag.ends_with("creator") {
                    meta.authors.push(trimmed.to_string());
                } else if current_tag.ends_with("description") && meta.description.is_none() {
                    meta.description = Some(trimmed.to_string());
                } else if current_tag.ends_with("language") && meta.language.is_none() {
                    meta.language = Some(trimmed.to_string());
                } else if current_tag.ends_with("subject") {
                    let tag = trimmed.to_string();
                    if !tag.is_empty() && !meta.tags.contains(&tag) {
                        meta.tags.push(tag);
                    }
                } else if current_tag == "meta" && epbu3_collection_id.is_some() && meta.series.is_none() {
                    meta.series = Some(trimmed.to_string());
                }
            }
            Ok(Event::End(_)) => {
                current_tag.clear();
            }
            Ok(Event::Eof) => break,
            Err(e) => return Err(Error::Metadata(format!("XML error reading EPUB OPF: {}", e))),
            _ => {}
        }
        buf.clear();
    }

    // Resolve cover href
    let mut cover_href: Option<String> = None;

    // 1. By cover_item_id from <meta name="cover" content="...">
    if let Some(ref target_id) = cover_item_id {
        if let Some((_, href, _, _)) = manifest_items.iter().find(|(id, _, _, _)| id == target_id) {
            cover_href = Some(href.clone());
        }
    }

    // 2. By properties="cover-image" (EPUB 3 standard)
    if cover_href.is_none() {
        if let Some((_, href, _, _)) = manifest_items.iter().find(|(_, _, _, props)| props.contains("cover-image")) {
            cover_href = Some(href.clone());
        }
    }

    // 3. By item id containing "cover" and media-type image
    if cover_href.is_none() {
        if let Some((_, href, _, _)) = manifest_items.iter().find(|(id, _, media, _)| {
            media.starts_with("image/") && id.to_lowercase().contains("cover")
        }) {
            cover_href = Some(href.clone());
        }
    }

    // 4. By href containing "cover" and media-type image
    if cover_href.is_none() {
        if let Some((_, href, _, _)) = manifest_items.iter().find(|(_, href, media, _)| {
            media.starts_with("image/") && href.to_lowercase().contains("cover")
        }) {
            cover_href = Some(href.clone());
        }
    }

    Ok((meta, cover_href))
}

fn normalize_zip_path(dir: &Path, href: &str) -> String {
    // URL decode href if needed
    let clean_href = href.replace("%20", " ");
    let mut p = dir.to_path_buf();
    for component in Path::new(&clean_href).components() {
        match component {
            std::path::Component::Normal(c) => p.push(c),
            std::path::Component::ParentDir => {
                p.pop();
            }
            _ => {}
        }
    }
    p.to_string_lossy().to_string().replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_opf_xml() {
        let opf = r#"<?xml version="1.0" encoding="utf-8"?>
        <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
          <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
            <dc:title>Foundation</dc:title>
            <dc:creator opf:role="aut">Isaac Asimov</dc:creator>
            <dc:description>The classic saga</dc:description>
            <dc:language>en</dc:language>
            <dc:subject>Science Fiction</dc:subject>
            <meta name="calibre:series" content="Foundation"/>
            <meta name="calibre:series_index" content="1.0"/>
            <meta name="cover" content="cover-image-id"/>
          </metadata>
          <manifest>
            <item id="cover-image-id" href="images/cover.jpg" media-type="image/jpeg"/>
            <item id="chapter1" href="chap1.html" media-type="application/xhtml+xml"/>
          </manifest>
        </package>"#;

        let (meta, cover_href) = parse_opf_xml(opf).unwrap();
        assert_eq!(meta.title, "Foundation");
        assert_eq!(meta.authors, vec!["Isaac Asimov"]);
        assert_eq!(meta.series.as_deref(), Some("Foundation"));
        assert_eq!(meta.series_index, Some(1));
        assert_eq!(meta.description.as_deref(), Some("The classic saga"));
        assert_eq!(meta.tags, vec!["Science Fiction"]);
        assert_eq!(cover_href.as_deref(), Some("images/cover.jpg"));
    }
}
