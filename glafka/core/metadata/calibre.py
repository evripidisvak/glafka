from pathlib import Path

from glafka.core.metadata import CoverImage, ExtractedMetadata
from glafka.core.metadata.epub import parse_opf


def parse_calibre_opf(opf_path: Path) -> ExtractedMetadata:
    """Read a Calibre book folder's metadata.opf and the cover.jpg next to it."""
    meta, _ = parse_opf(opf_path.read_bytes())
    if not meta.title:
        meta.title = opf_path.parent.name or "Unknown Title"

    cover = opf_path.parent / "cover.jpg"
    if cover.is_file():
        try:
            meta.cover_image = CoverImage(mime_type="image/jpeg", data=cover.read_bytes())
        except OSError:
            pass
    return meta
