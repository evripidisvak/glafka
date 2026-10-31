import posixpath
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote

from glafka.core.errors import MetadataError
from glafka.core.metadata import CoverImage, ExtractedMetadata, parse_series_index

# (id, href, media-type, properties)
_ManifestItem = tuple[str, str, str, str]


def _local(tag: str) -> str:
    """'{http://purl.org/dc/elements/1.1/}title' -> 'title'."""
    return tag.rsplit("}", 1)[-1]


def parse_epub(epub_path: Path) -> ExtractedMetadata:
    try:
        with zipfile.ZipFile(epub_path) as zf:
            opf_path = find_opf_path(zf)
            try:
                opf_xml = zf.read(opf_path)
            except KeyError as e:
                raise MetadataError(f"Could not read OPF '{opf_path}'") from e
            meta, cover_href = parse_opf(opf_xml)

            if not meta.title:
                meta.title = epub_path.stem or "Unknown Title"

            if cover_href:
                cover_name = _resolve_href(posixpath.dirname(opf_path), cover_href)
                try:
                    data = zf.read(cover_name)
                except KeyError:
                    data = None
                if data is not None:
                    mime = "image/png" if cover_name.endswith(".png") else "image/jpeg"
                    meta.cover_image = CoverImage(mime_type=mime, data=data)
            return meta
    except zipfile.BadZipFile as e:
        raise MetadataError(f"Invalid EPUB/ZIP archive: {e}") from e


def parse_opf(xml: bytes | str) -> tuple[ExtractedMetadata, str | None]:
    """Metadata plus the cover image's href (relative to the OPF file), if one is declared."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise MetadataError(f"XML error reading OPF: {e}") from e

    meta = ExtractedMetadata()
    cover_id: str | None = None
    manifest: list[_ManifestItem] = []

    for elem in root.iter():
        name = _local(elem.tag)
        text = (elem.text or "").strip()

        if name == "title" and text and not meta.title:
            meta.title = text
        elif name == "creator" and text:
            meta.authors.append(text)
        elif name == "description" and text and meta.description is None:
            meta.description = text
        elif name == "language" and text and meta.language is None:
            meta.language = text
        elif name == "subject" and text and text not in meta.tags:
            meta.tags.append(text)
        elif name == "item":
            manifest.append(
                (
                    elem.get("id", ""),
                    elem.get("href", ""),
                    elem.get("media-type", ""),
                    elem.get("properties", ""),
                )
            )
        elif name == "meta":
            meta_name = elem.get("name", "")
            content = elem.get("content", "")
            prop = elem.get("property", "")
            if meta_name == "calibre:series":
                meta.series = content.strip() or None
            elif meta_name == "calibre:series_index":
                index = parse_series_index(content)
                if index is not None:
                    meta.series_index = index
            elif meta_name == "cover" and content:
                cover_id = content
            # EPUB 3 puts collection name and position in the element text, not `content`
            elif prop == "belongs-to-collection" and text and meta.series is None:
                meta.series = text
            elif prop == "group-position" and elem.get("refines") and text:
                index = parse_series_index(text)
                if index is not None:
                    meta.series_index = index

    return meta, _find_cover_href(cover_id, manifest)


def _find_cover_href(cover_id: str | None, manifest: list[_ManifestItem]) -> str | None:
    # 1. <meta name="cover" content="item-id"> (EPUB 2)
    if cover_id:
        for item_id, href, _, _ in manifest:
            if item_id == cover_id:
                return href
    # 2. properties="cover-image" (EPUB 3)
    for _, href, _, props in manifest:
        if "cover-image" in props.split():
            return href
    # 3. and 4. Heuristics: an image whose id, then whose href, mentions "cover"
    for item_id, href, media, _ in manifest:
        if media.startswith("image/") and "cover" in item_id.lower():
            return href
    for _, href, media, _ in manifest:
        if media.startswith("image/") and "cover" in href.lower():
            return href
    return None


def find_opf_path(zf: zipfile.ZipFile) -> str:
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        for elem in container.iter():
            full_path = elem.get("full-path")
            if _local(elem.tag) == "rootfile" and full_path:
                return full_path
    except (KeyError, ET.ParseError):
        pass

    names = zf.namelist()
    for candidate in ("OEBPS/content.opf", "content.opf", "OPS/package.opf"):
        if candidate in names:
            return candidate
    for name in names:
        if name.endswith(".opf"):
            return name
    raise MetadataError("Could not locate OPF package document in EPUB")


def _resolve_href(opf_dir: str, href: str) -> str:
    """Zip entry name for an href relative to the OPF's folder."""
    return posixpath.normpath(posixpath.join(opf_dir, unquote(href)))
