import shutil
from pathlib import Path

import pytest

from glafka.core.db import Database
from glafka.core.importer import ImportStatus, import_file
from glafka.core.metadata.pdf import parse_pdf

pytestmark = pytest.mark.skipif(
    shutil.which("pdfinfo") is None or shutil.which("pdftoppm") is None,
    reason="poppler-utils not installed",
)


def _pdf_string(text: str) -> bytes:
    """PDF text string: UTF-16BE with BOM, so any language survives."""
    return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode() + b">"


def make_pdf(path: Path, title: str | None = None, author: str | None = None) -> Path:
    """A valid one-page PDF (a blue rectangle) with optional document info."""
    content = b"0 0 1 rg 20 20 160 260 re f"
    info = b"<< "
    if title is not None:
        info += b"/Title " + _pdf_string(title) + b" "
    if author is not None:
        info += b"/Author " + _pdf_string(author) + b" "
    info += b">>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 300] /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        info,
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 5 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    path.write_bytes(out)
    return path


def test_title_first_author_and_cover(tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf", "Ελληνική Μυθολογία", "Sergio Pereira; Someone Else")

    meta = parse_pdf(pdf)

    assert meta.title == "Ελληνική Μυθολογία"
    assert meta.authors == ["Sergio Pereira"]
    assert meta.cover_image is not None
    assert meta.cover_image.data[:2] == b"\xff\xd8"  # a JPEG


def test_comma_in_author_is_kept(tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf", "Dune", "Herbert, Frank")

    assert parse_pdf(pdf).authors == ["Herbert, Frank"]


@pytest.mark.parametrize("junk", ["Microsoft Word - thesis.docx", "Untitled", "chapter1.tex"])
def test_junk_titles_fall_back_to_the_file_name(tmp_path, junk):
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    db = Database.open(library_dir / "test.db", root=library_dir)
    pdf = make_pdf(tmp_path / "Frank Herbert - Dune.pdf", junk, None)

    book = import_file(pdf, library_dir, db).book

    assert (book.title, book.author) == ("Dune", "Frank Herbert")


def test_import_uses_pdf_metadata_and_first_page_cover(tmp_path):
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    db = Database.open(library_dir / "test.db", root=library_dir)
    pdf = make_pdf(tmp_path / "download-123.pdf", "Generative AI", "Sergio Pereira;")

    result = import_file(pdf, library_dir, db)

    assert result.status is ImportStatus.IMPORTED
    assert (result.book.title, result.book.author) == ("Generative AI", "Sergio Pereira")
    assert result.book.cover_path is not None
    assert Path(result.book.cover_path).read_bytes()[:2] == b"\xff\xd8"


def test_pdf_without_info_still_gets_a_cover(tmp_path):
    meta = parse_pdf(make_pdf(tmp_path / "x.pdf"))

    assert (meta.title, meta.authors) == ("", [])
    assert meta.cover_image is not None


def test_broken_pdf_gives_nothing(tmp_path):
    broken = tmp_path / "x.pdf"
    broken.write_bytes(b"%PDF-1.4 not really")

    meta = parse_pdf(broken)

    assert (meta.title, meta.authors, meta.cover_image) == ("", [], None)
