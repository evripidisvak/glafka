import zipfile
from pathlib import Path

import pytest
from test_importer import CONTAINER

from glafka.core.errors import MetadataError
from glafka.core.metadata.epub import parse_epub
from glafka.core.metadata.epub_writer import write_epub_metadata
from glafka.core.models import Book

EPUB2_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="uid">urn:uuid:1234</dc:identifier>
    <dc:title>Old Title</dc:title>
    <dc:creator opf:role="aut" opf:file-as="Author, Old">Old Author</dc:creator>
    <dc:creator opf:role="aut">Co Author</dc:creator>
    <dc:subject>Old Tag</dc:subject>
    <dc:language>en</dc:language>
    <meta name="cover" content="cover-img"/>
  </metadata>
  <manifest>
    <item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover-img" href="cover.jpg" media-type="image/jpeg"/>
  </manifest>
  <spine><itemref idref="text"/></spine>
</package>"""

EPUB3_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:5678</dc:identifier>
    <dc:title>The Trespasser</dc:title>
    <dc:creator id="c1">Tana French</dc:creator>
    <meta refines="#c1" property="file-as">French, Tana</meta>
    <meta property="dcterms:modified">2020-01-01T00:00:00Z</meta>
    <meta property="belongs-to-collection" id="c01">Dublin Murder Squad</meta>
    <meta refines="#c01" property="collection-type">series</meta>
    <meta refines="#c01" property="group-position">6</meta>
  </metadata>
  <manifest><item id="text" href="text.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="text"/></spine>
</package>"""


def make_epub(path: Path, opf: str, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("content.opf", opf)
        zf.writestr("text.xhtml", b"<html>chapter one</html>", zipfile.ZIP_DEFLATED)
        zf.writestr("cover.jpg", b"\xff\xd8 not really a jpeg")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return path


def edited(**changes) -> Book:
    fields = dict(
        id="b",
        title="New Title",
        author="New Author",
        series="New Series",
        series_index=3,
        description="A new description",
        tags=["Sci-Fi", "Classic"],
    )
    fields.update(changes)
    return Book(**fields)


def entries(path: Path) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as zf:
        return [(i.filename, zf.read(i)) for i in zf.infolist()]


def opf_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("content.opf").decode()


def test_written_metadata_reads_back(tmp_path):
    epub = make_epub(tmp_path / "b.epub", EPUB2_OPF)

    write_epub_metadata(epub, edited())

    meta = parse_epub(epub)
    assert meta.title == "New Title"
    assert meta.authors == ["New Author", "Co Author"]  # co-author kept
    assert (meta.series, meta.series_index) == ("New Series", 3)
    assert meta.description == "A new description"
    assert meta.tags == ["Sci-Fi", "Classic"]
    assert meta.language == "en"  # untouched fields stay
    assert meta.cover_image is not None


def test_everything_but_the_opf_is_unchanged_and_mimetype_comes_first(tmp_path):
    epub = make_epub(tmp_path / "b.epub", EPUB2_OPF)
    before = entries(epub)

    write_epub_metadata(epub, edited())

    after = entries(epub)
    assert [n for n, _ in after] == [n for n, _ in before]
    assert [e for e in after if e[0] != "content.opf"] == [
        e for e in before if e[0] != "content.opf"
    ]
    with zipfile.ZipFile(epub) as zf:
        first = zf.infolist()[0]
        assert (first.filename, first.compress_type) == ("mimetype", zipfile.ZIP_STORED)
        assert first.extra == b""


def test_opf_keeps_its_namespace_prefixes(tmp_path):
    epub = make_epub(tmp_path / "b.epub", EPUB2_OPF)

    write_epub_metadata(epub, edited())

    text = opf_text(epub)
    assert "<package " in text and "<dc:title>New Title</dc:title>" in text
    assert "ns0:" not in text


def test_changed_author_loses_the_stale_sort_name(tmp_path):
    epub = make_epub(tmp_path / "b.epub", EPUB3_OPF)

    write_epub_metadata(epub, edited(series="Dublin Murder Squad", series_index=6))

    text = opf_text(epub)
    assert "French, Tana" not in text
    assert '<dc:creator id="c1">New Author</dc:creator>' in text


def test_epub3_series_updated_and_modified_date_set(tmp_path):
    epub = make_epub(tmp_path / "b.epub", EPUB3_OPF)

    write_epub_metadata(epub, edited(series="Dublin Murder Squad", series_index=7))

    meta = parse_epub(epub)
    assert (meta.series, meta.series_index) == ("Dublin Murder Squad", 7)
    text = opf_text(epub)
    assert text.count("belongs-to-collection") == 1
    assert "2020-01-01T00:00:00Z" not in text  # dcterms:modified refreshed


def test_removing_the_series_removes_every_trace(tmp_path):
    epub = make_epub(tmp_path / "b.epub", EPUB3_OPF)

    write_epub_metadata(epub, edited(series=None, series_index=None))

    text = opf_text(epub)
    for gone in ("belongs-to-collection", "#c01", "calibre:series", "Dublin Murder Squad"):
        assert gone not in text
    assert parse_epub(epub).series is None


def test_drm_protected_epub_is_left_alone(tmp_path):
    encryption = b"""<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
        xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
      <enc:EncryptedData><enc:EncryptionMethod Algorithm="http://ns.adobe.com/adept"/>
      </enc:EncryptedData></encryption>"""
    epub = make_epub(tmp_path / "b.epub", EPUB2_OPF, {"META-INF/encryption.xml": encryption})
    before = epub.read_bytes()

    with pytest.raises(MetadataError, match="DRM"):
        write_epub_metadata(epub, edited())
    assert epub.read_bytes() == before


def test_font_obfuscation_is_not_drm(tmp_path):
    encryption = b"""<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
        xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
      <enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
      </enc:EncryptedData></encryption>"""
    epub = make_epub(tmp_path / "b.epub", EPUB2_OPF, {"META-INF/encryption.xml": encryption})

    write_epub_metadata(epub, edited())

    assert parse_epub(epub).title == "New Title"


def test_failure_mid_write_leaves_the_original_untouched(tmp_path, monkeypatch):
    epub = make_epub(tmp_path / "b.epub", EPUB2_OPF)
    before = epub.read_bytes()

    def disk_full(*_args, **_kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr("glafka.core.metadata.epub_writer.shutil.copyfileobj", disk_full)
    with pytest.raises(OSError):
        write_epub_metadata(epub, edited())

    assert epub.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.epub"]  # no temp file left


def test_not_an_epub_raises_metadata_error(tmp_path):
    fake = tmp_path / "b.epub"
    fake.write_bytes(b"not a zip")

    with pytest.raises(MetadataError):
        write_epub_metadata(fake, edited())


def test_new_elements_match_the_files_style(tmp_path):
    # Like the real file that showed the problem: OPF is the default namespace AND bound to opf:
    epub = make_epub(tmp_path / "b.epub", EPUB2_OPF)

    write_epub_metadata(epub, edited())

    text = opf_text(epub)
    assert "opf:meta" not in text
    assert '    <meta name="calibre:series" content="New Series"/>\n' in text  # own line, indented
    assert text.rstrip().endswith("</package>")
