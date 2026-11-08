"""Write edited metadata into an EPUB's package document (OPF), leaving everything else as is.

lxml, not xml.etree: etree rewrites namespace prefixes (<ns0:package>), which some readers reject.
"""

import os
import posixpath
import re
import shutil
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from lxml import etree

from glafka.core.errors import MetadataError
from glafka.core.metadata import CoverImage
from glafka.core.metadata.epub import find_cover_item, find_opf_path, resolve_href
from glafka.core.models import Book

OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
_ENC = "http://www.w3.org/2001/04/xmlenc#"
# Font obfuscation, not DRM: keyed on the book's identifier, which is never changed here
_FONT_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
_SERIES_ID = "glafka-series"

# OPFs come from anywhere: no entity expansion, no DTDs, no network
_PARSER = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)


def write_epub_metadata(epub: Path, book: Book, cover: CoverImage | None = None) -> None:
    """Put the book's title, author, series, description and tags into the EPUB,
    and the cover image when one is given.

    The file is replaced in one step; on any failure the original is untouched.
    Raises MetadataError for EPUBs that can't or mustn't be changed (unreadable, DRM).
    """
    try:
        with zipfile.ZipFile(epub) as zin:
            _refuse_drm(zin)
            opf_name = find_opf_path(zin)
            try:
                package = etree.fromstring(zin.read(opf_name), _PARSER)
            except (KeyError, etree.XMLSyntaxError) as e:
                raise MetadataError(f"Unreadable package document {opf_name}: {e}") from e
            _apply(package, book)
            replace: dict[str, bytes] = {}
            add: dict[str, bytes] = {}
            if cover is not None:
                replace, add = _embed_cover(package, opf_name, set(zin.namelist()), cover)
            replace[opf_name] = _serialize(package)
            _rewrite_zip(zin, epub, replace, add)
    except zipfile.BadZipFile as e:
        raise MetadataError(f"Not a valid EPUB: {e}") from e


def _serialize(package: etree._Element) -> bytes:
    opf = etree.tostring(package.getroottree(), xml_declaration=True, encoding="utf-8")
    # libxml2 names new OPF elements after the nearest declaration, "opf:meta" when <metadata>
    # also declares xmlns:opf. Where OPF is the default namespace, bare <meta> means the same
    # and matches the file's existing elements.
    if package.nsmap.get(None) == OPF:
        opf = re.sub(rb"<(/?)opf:meta\b", rb"<\1meta", opf)
    return opf


def _append(parent: etree._Element, tag: str, **attributes: str) -> etree._Element:
    """New last child laid out like its siblings: one per line, same indentation."""
    element = etree.SubElement(parent, tag, **attributes)
    previous = element.getprevious()
    if previous is not None and parent.text is not None and not parent.text.strip():
        element.tail = previous.tail  # the indentation before </metadata>
        previous.tail = parent.text  # the indentation of a child
    return element


def _refuse_drm(zin: zipfile.ZipFile) -> None:
    try:
        encryption = etree.fromstring(zin.read("META-INF/encryption.xml"), _PARSER)
    except KeyError:
        return
    algorithms = {m.get("Algorithm") for m in encryption.iter(f"{{{_ENC}}}EncryptionMethod")}
    if algorithms - _FONT_OBFUSCATION:
        raise MetadataError("The EPUB is DRM-protected; it is left unchanged")


def _rewrite_zip(
    zin: zipfile.ZipFile, epub: Path, replace: dict[str, bytes], add: dict[str, bytes]
) -> None:
    """Every entry copied as is, except the ones in `replace`; then the new ones in `add`."""
    tmp = epub.with_name(f".{epub.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w") as zout:
            # EPUB rule: "mimetype" first, stored uncompressed, no extra field
            zout.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip")
            for info in zin.infolist():
                if info.filename == "mimetype":
                    continue
                entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                entry.compress_type = info.compress_type
                entry.external_attr = info.external_attr
                if info.filename in replace:
                    zout.writestr(entry, replace[info.filename])
                elif info.is_dir():
                    zout.writestr(entry, b"")
                else:
                    entry.file_size = info.file_size  # lets zipfile pick zip64 for huge entries
                    # Streamed, not read(): a 60 MB illustrated book must not sit in memory
                    with zin.open(info) as src, zout.open(entry, "w") as dst:
                        shutil.copyfileobj(src, dst)
            for name, data in add.items():  # stored: images don't compress
                zout.writestr(zipfile.ZipInfo(name, date_time=time.localtime()[:6]), data)
        os.replace(tmp, epub)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _embed_cover(
    package: etree._Element, opf_name: str, names: set[str], cover: CoverImage
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """(entries to replace, entries to add). Same format as the current cover: its bytes are
    swapped in place, so pages showing it follow. Otherwise a new image becomes the cover and
    the old one stays for whatever page uses it."""
    manifest = package.find(f"{{{OPF}}}manifest")
    metadata = package.find(f"{{{OPF}}}metadata")
    if manifest is None or metadata is None:
        raise MetadataError("The package document has no <manifest> or <metadata>")
    elements = manifest.findall(f"{{{OPF}}}item")
    items = [
        (e.get("id", ""), e.get("href", ""), e.get("media-type", ""), e.get("properties", ""))
        for e in elements
    ]
    cover_id = next((m.get("content") for m in _metas(metadata) if m.get("name") == "cover"), None)
    opf_dir = posixpath.dirname(opf_name)
    current = find_cover_item(cover_id, items)
    if current is not None and current[2] == cover.mime_type:
        entry = resolve_href(opf_dir, current[1])
        if entry in names:
            return {entry: cover.data}, {}

    ext = "png" if cover.mime_type == "image/png" else "jpg"
    taken_ids = {item[0] for item in items}
    n = 1
    while True:
        stem = "glafka-cover" if n == 1 else f"glafka-cover-{n}"
        if stem not in taken_ids and resolve_href(opf_dir, f"{stem}.{ext}") not in names:
            break
        n += 1
    for element in elements:  # only one item may carry the cover-image property
        tokens = element.get("properties", "").split()
        if "cover-image" in tokens:
            tokens.remove("cover-image")
            if tokens:
                element.set("properties", " ".join(tokens))
            else:
                del element.attrib["properties"]
    new = _append(manifest, f"{{{OPF}}}item", id=stem, href=f"{stem}.{ext}")
    new.set("media-type", cover.mime_type)
    if package.get("version", "").startswith("3"):
        new.set("properties", "cover-image")
    _set_named_meta(metadata, "cover", stem)  # EPUB 2, and EPUB 3 readers that only look here
    return {}, {resolve_href(opf_dir, f"{stem}.{ext}"): cover.data}


def _apply(package: etree._Element, book: Book) -> None:
    metadata = package.find(f"{{{OPF}}}metadata")
    if metadata is None:
        raise MetadataError("The package document has no <metadata>")
    _set_first(metadata, f"{{{DC}}}title", book.title)
    _set_creator(metadata, book.author)
    if book.description:
        _set_first(metadata, f"{{{DC}}}description", book.description)
    else:
        _remove_all(metadata, f"{{{DC}}}description")
    _remove_all(metadata, f"{{{DC}}}subject")
    for tag in book.tags:
        _append(metadata, f"{{{DC}}}subject").text = tag
    _set_calibre_series(metadata, book.series, book.series_index)
    if package.get("version", "").startswith("3"):
        _set_epub3_series(metadata, book.series, book.series_index)
        _set_meta_property(metadata, "dcterms:modified", _now())


def _set_first(metadata: etree._Element, tag: str, text: str) -> None:
    element = metadata.find(tag)
    if element is None:
        element = _append(metadata, tag)
    element.text = text


def _remove_all(metadata: etree._Element, tag: str) -> None:
    for element in metadata.findall(tag):
        metadata.remove(element)


def _set_creator(metadata: etree._Element, author: str) -> None:
    """Replace the first author only; co-authors stay."""
    first = metadata.find(f"{{{DC}}}creator")
    if first is None:
        _append(metadata, f"{{{DC}}}creator").text = author
        return
    if (first.text or "").strip() == author:
        return
    first.text = author
    # A stale sort name ("Herbert, Frank" for someone else) is worse than none
    first.attrib.pop(f"{{{OPF}}}file-as", None)
    if creator_id := first.get("id"):
        _remove_refining(metadata, creator_id, "file-as")


def _metas(metadata: etree._Element) -> list[etree._Element]:
    return metadata.findall(f"{{{OPF}}}meta")


def _remove_refining(metadata: etree._Element, element_id: str, prop: str | None = None) -> None:
    for meta in _metas(metadata):
        if meta.get("refines") == f"#{element_id}" and (
            prop is None or meta.get("property") == prop
        ):
            metadata.remove(meta)


def _set_named_meta(metadata: etree._Element, name: str, content: str | None) -> None:
    """EPUB 2 style <meta name=... content=...>; None removes it."""
    existing = [m for m in _metas(metadata) if m.get("name") == name]
    for meta in existing[1:] if content is not None else existing:
        metadata.remove(meta)
    if content is None:
        return
    if existing:
        existing[0].set("content", content)
    else:
        _append(metadata, f"{{{OPF}}}meta", name=name, content=content)


def _set_calibre_series(metadata: etree._Element, series: str | None, index: int | None) -> None:
    _set_named_meta(metadata, "calibre:series", series)
    _set_named_meta(
        metadata, "calibre:series_index", str(index) if series and index is not None else None
    )


def _set_epub3_series(metadata: etree._Element, series: str | None, index: int | None) -> None:
    collection = next(
        (m for m in _metas(metadata) if m.get("property") == "belongs-to-collection"), None
    )
    if series is None:
        if collection is not None:
            if collection.get("id"):  # its refinements would dangle otherwise
                _remove_refining(metadata, collection.get("id"))
            metadata.remove(collection)
        return
    if collection is None:
        collection = etree.SubElement(
            metadata, f"{{{OPF}}}meta", property="belongs-to-collection", id=_SERIES_ID
        )
    collection.text = series
    collection_id = collection.get("id")
    if not collection_id:
        collection_id = _SERIES_ID
        collection.set("id", collection_id)
    _set_refinement(metadata, collection_id, "collection-type", "series")
    _set_refinement(metadata, collection_id, "group-position", str(index) if index else None)


def _set_refinement(metadata: etree._Element, element_id: str, prop: str, text: str | None) -> None:
    _remove_refining(metadata, element_id, prop)
    if text is not None:
        meta = _append(metadata, f"{{{OPF}}}meta", refines=f"#{element_id}", property=prop)
        meta.text = text


def _set_meta_property(metadata: etree._Element, prop: str, text: str) -> None:
    meta = next(
        (m for m in _metas(metadata) if m.get("property") == prop and not m.get("refines")), None
    )
    if meta is None:
        meta = _append(metadata, f"{{{OPF}}}meta", property=prop)
    meta.text = text


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
