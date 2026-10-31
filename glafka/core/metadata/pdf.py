"""PDF title, author and cover, read with poppler-utils (pdfinfo, pdftoppm).

Command-line tools rather than a library: installed with GNOME, fast and robust, and no
AGPL dependency (PyMuPDF). Missing tools or a broken PDF just mean less metadata.
"""

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from glafka.core.metadata import CoverImage, ExtractedMetadata

log = logging.getLogger(__name__)

_TIMEOUT = 60  # seconds; a pathological PDF must not hang an import
_COVER_SIZE = 1200  # longest side of the rendered first page, in pixels
# Titles that name the file or the authoring tool, not the book
_JUNK_TITLE = re.compile(
    r"^(untitled|microsoft (word|powerpoint) - .*)$|\.(docx?|odt|rtf|tex|dvi|pdf|indd|qxd)$",
    re.IGNORECASE,
)


def parse_pdf(path: Path) -> ExtractedMetadata:
    """Title and first author from the document info, cover from page 1; empty if unreadable."""
    meta = ExtractedMetadata()
    info = _pdfinfo(path)
    title = info.get("Title", "").strip()
    if title and not _JUNK_TITLE.search(title):
        meta.title = title
    # "A; B" lists several authors; commas stay, they may be "Last, First"
    author = next((a.strip() for a in info.get("Author", "").split(";") if a.strip()), "")
    if author:
        meta.authors = [author]
    meta.cover_image = _render_first_page(path)
    return meta


def _run(args: list[str]) -> subprocess.CompletedProcess[bytes] | None:
    if shutil.which(args[0]) is None:
        log.info("%s not installed; PDF metadata limited to the file name", args[0])
        return None
    try:
        result = subprocess.run(args, capture_output=True, timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("%s failed: %s", args[0], e)
        return None
    if result.returncode != 0:
        log.info("%s could not read the PDF: %s", args[0], result.stderr.decode(errors="replace"))
        return None
    return result


def _pdfinfo(path: Path) -> dict[str, str]:
    result = _run(["pdfinfo", "-enc", "UTF-8", "--", str(path)])
    if result is None:
        return {}
    info = {}
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            info[key.strip()] = value.strip()
    return info


def _render_first_page(path: Path) -> CoverImage | None:
    with tempfile.TemporaryDirectory(prefix="glafka-pdf-") as tmp:
        out = Path(tmp) / "cover"
        args = ["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-jpeg", "-jpegopt"]
        args += ["quality=85", "-scale-to", str(_COVER_SIZE), "--", str(path), str(out)]
        if _run(args) is None:
            return None
        jpeg = out.with_suffix(".jpg")
        if not jpeg.is_file():
            return None
        return CoverImage(mime_type="image/jpeg", data=jpeg.read_bytes())
