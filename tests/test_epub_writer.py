import zipfile
from pathlib import Path

import pytest
from test_importer import CONTAINER

from glafka.core.errors import MetadataError
from glafka.core.metadata import CoverImage
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


JPEG = CoverImage(mime_type="image/jpeg", data=b"\xff\xd8 a new jpeg cover")


def opf_of(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("content.opf").decode()


def test_cover_of_the_same_format_is_replaced_in_place(tmp_path):
    epub = make_epub(tmp_path / "b.epub", EPUB2_OPF)
    before = dict(entries(epub))

    write_epub_metadata(epub, edited(), cover=JPEG)

    after = dict(entries(epub))
    assert after.keys() == before.keys()  # same entries: pages showing cover.jpg follow
    assert after["cover.jpg"] == JPEG.data
    assert after["text.xhtml"] == before["text.xhtml"]
    assert parse_epub(epub).cover_image.data == JPEG.data


def test_epub3_without_a_cover_gets_one_marked_both_ways(tmp_path):
    epub = make_epub(tmp_path / "b.epub", EPUB3_OPF)

    write_epub_metadata(epub, edited(), cover=JPEG)

    opf = opf_of(epub)
    assert (
        '<item id="glafka-cover" href="glafka-cover.jpg" media-type="image/jpeg" '
        'properties="cover-image"/>' in opf
    )
    assert '<meta name="cover" content="glafka-cover"/>' in opf
    assert dict(entries(epub))["glafka-cover.jpg"] == JPEG.data
    assert parse_epub(epub).cover_image.data == JPEG.data


def test_epub2_without_a_cover_gets_the_meta_but_no_epub3_property(tmp_path):
    opf = EPUB2_OPF.replace('    <meta name="cover" content="cover-img"/>\n', "").replace(
        '    <item id="cover-img" href="cover.jpg" media-type="image/jpeg"/>\n', ""
    )
    epub = make_epub(tmp_path / "b.epub", opf, extra={"glafka-cover.jpg": b"taken"})

    write_epub_metadata(epub, edited(), cover=JPEG)

    written = opf_of(epub)
    assert 'href="glafka-cover-2.jpg"' in written  # glafka-cover.jpg was already in the zip
    assert "properties=" not in written
    assert '<meta name="cover" content="glafka-cover-2"/>' in written
    assert dict(entries(epub))["glafka-cover.jpg"] == b"taken"
    assert parse_epub(epub).cover_image.data == JPEG.data


def test_png_cover_replaced_by_jpeg_moves_the_marker_and_keeps_the_old_image(tmp_path):
    opf = EPUB3_OPF.replace(
        '<manifest><item id="text"',
        '<manifest><item id="c" href="images/cover.png" media-type="image/png" '
        'properties="cover-image svg"/><item id="text"',
    )
    epub = make_epub(tmp_path / "b.epub", opf, extra={"images/cover.png": b"old png"})

    write_epub_metadata(epub, edited(), cover=JPEG)

    written = opf_of(epub)
    assert (
        '<item id="c" href="images/cover.png" media-type="image/png" properties="svg"/>' in written
    )
    assert 'properties="cover-image"' in written and 'id="glafka-cover"' in written
    assert dict(entries(epub))["images/cover.png"] == b"old png"  # a cover page may show it
    assert parse_epub(epub).cover_image.data == JPEG.data


TITLEPAGE = """<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Cover</title></head>
<body><div>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1"
 width="100%" height="100%" viewBox="0 0 325 500" preserveAspectRatio="none">
<image width="325" height="500" xlink:href="cover.jpg"/>
</svg>
</div></body>
</html>"""


def test_in_place_swap_fits_a_calibre_cover_page_to_the_new_size(tmp_path):
    opf = EPUB2_OPF.replace(
        '<item id="text"',
        '<item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>\n'
        '    <item id="text"',
    )
    epub = make_epub(tmp_path / "b.epub", opf, extra={"titlepage.xhtml": TITLEPAGE.encode()})
    before = dict(entries(epub))

    write_epub_metadata(epub, edited(), cover=CoverImage("image/jpeg", b"\xff\xd8 new", 1067, 1600))

    page = dict(entries(epub))["titlepage.xhtml"].decode()
    assert 'viewBox="0 0 1067 1600"' in page
    assert '<image width="1067" height="1600" xlink:href="cover.jpg"/>' in page
    assert 'preserveAspectRatio="none"' in page and "<!DOCTYPE html>" in page
    assert dict(entries(epub))["text.xhtml"] == before["text.xhtml"]


def test_an_image_only_guessed_from_its_name_is_never_overwritten(tmp_path):
    opf = EPUB3_OPF.replace(
        '<manifest><item id="text"',
        '<manifest><item id="back" href="images/backcover.jpg" media-type="image/jpeg"/>'
        '<item id="text"',
    )
    epub = make_epub(tmp_path / "b.epub", opf, extra={"images/backcover.jpg": b"back cover art"})

    write_epub_metadata(epub, edited(), cover=JPEG)

    assert dict(entries(epub))["images/backcover.jpg"] == b"back cover art"
    written = opf_of(epub)
    assert 'id="glafka-cover"' in written and 'properties="cover-image"' in written
    assert parse_epub(epub).cover_image.data == JPEG.data


def test_markers_that_disagree_both_move_to_the_new_cover(tmp_path):
    opf = EPUB3_OPF.replace(
        '<meta property="dcterms:modified">',
        '<meta name="cover" content="a"/>\n    <meta property="dcterms:modified">',
    ).replace(
        '<manifest><item id="text"',
        '<manifest><item id="a" href="a.jpg" media-type="image/jpeg"/>'
        '<item id="b" href="b.jpg" media-type="image/jpeg" properties="cover-image"/>'
        '<item id="text"',
    )
    epub = make_epub(tmp_path / "b.epub", opf, extra={"a.jpg": b"meta's", "b.jpg": b"property's"})

    write_epub_metadata(epub, edited(), cover=JPEG)

    written, files = opf_of(epub), dict(entries(epub))
    assert (files["a.jpg"], files["b.jpg"]) == (b"meta's", b"property's")
    assert '<meta name="cover" content="glafka-cover"/>' in written
    assert '<item id="b" href="b.jpg" media-type="image/jpeg"/>' in written
    assert (
        'id="glafka-cover" href="glafka-cover.jpg" media-type="image/jpeg" properties="cover-image"'
        in written
    )


def test_reader_and_writer_both_take_the_first_cover_marker(tmp_path):
    opf = EPUB2_OPF.replace(
        '<meta name="cover" content="cover-img"/>',
        '<meta name="cover" content="cover-img"/>\n    <meta name="cover" content="png-cover"/>',
    ).replace(
        '<item id="text"',
        '<item id="png-cover" href="cover.png" media-type="image/png"/>\n    <item id="text"',
    )
    epub = make_epub(tmp_path / "b.epub", opf, extra={"cover.png": b"png"})
    names = set(dict(entries(epub)))
    assert parse_epub(epub).cover_image.data == b"\xff\xd8 not really a jpeg"

    write_epub_metadata(epub, edited(), cover=JPEG)

    assert set(dict(entries(epub))) == names  # swapped in place, nothing added
    assert dict(entries(epub))["cover.jpg"] == JPEG.data


def test_image_jpg_counts_as_jpeg(tmp_path):
    opf = EPUB2_OPF.replace(
        'href="cover.jpg" media-type="image/jpeg"', 'href="cover.jpg" media-type="image/jpg"'
    )
    epub = make_epub(tmp_path / "b.epub", opf)
    names = set(dict(entries(epub)))
    assert parse_epub(epub).cover_image.data == b"\xff\xd8 not really a jpeg"  # reader sees it

    used = write_epub_metadata(epub, edited(), cover=JPEG)

    assert used is JPEG
    assert set(dict(entries(epub))) == names and dict(entries(epub))["cover.jpg"] == JPEG.data


def _with_page(page: str, cover_href: str = "cover.jpg") -> str:
    """EPUB 2 OPF with a cover page; the cover item's href is `cover_href`."""
    return EPUB2_OPF.replace('href="cover.jpg"', f'href="{cover_href}"').replace(
        '<item id="text"',
        '<item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>\n'
        '    <item id="text"',
    )


def test_cover_page_sized_relative_to_the_page_is_left_alone(tmp_path):
    page = TITLEPAGE.replace(' viewBox="0 0 325 500"', "").replace(
        '<image width="325" height="500"', '<image width="100%" height="100%"'
    )
    epub = make_epub(
        tmp_path / "b.epub", _with_page(page), extra={"titlepage.xhtml": page.encode()}
    )

    write_epub_metadata(epub, edited(), cover=CoverImage("image/jpeg", b"\xff\xd8 new", 1067, 1600))

    assert dict(entries(epub))["titlepage.xhtml"] == page.encode()  # untouched


def test_relative_image_size_inside_a_fixed_box_is_kept(tmp_path):
    page = TITLEPAGE.replace('<image width="325" height="500"', '<image width="100%" height="100%"')
    epub = make_epub(
        tmp_path / "b.epub", _with_page(page), extra={"titlepage.xhtml": page.encode()}
    )

    write_epub_metadata(epub, edited(), cover=CoverImage("image/jpeg", b"\xff\xd8 new", 1067, 1600))

    written = dict(entries(epub))["titlepage.xhtml"].decode()
    assert 'viewBox="0 0 1067 1600"' in written and 'width="100%" height="100%"' in written


def test_percent_encoded_cover_name_on_the_page_is_found(tmp_path):
    page = TITLEPAGE.replace('xlink:href="cover.jpg"', 'xlink:href="my%20cover.jpg"')
    opf = _with_page(page, cover_href="my%20cover.jpg")
    epub = make_epub(
        tmp_path / "b.epub", opf, extra={"titlepage.xhtml": page.encode(), "my cover.jpg": b"old"}
    )

    write_epub_metadata(epub, edited(), cover=CoverImage("image/jpeg", b"\xff\xd8 new", 800, 1200))

    files = dict(entries(epub))
    assert files["my cover.jpg"] == b"\xff\xd8 new"
    assert 'viewBox="0 0 800 1200"' in files["titlepage.xhtml"].decode()


def test_each_epub_gets_the_format_of_its_own_cover(tmp_path):
    opf = EPUB3_OPF.replace(
        '<manifest><item id="text"',
        '<manifest><item id="c" href="cover.png" media-type="image/png" properties="cover-image"/>'
        '<item id="text"',
    )
    epub = make_epub(tmp_path / "b.epub", opf, extra={"cover.png": b"old png"})
    names = set(dict(entries(epub)))
    png = CoverImage("image/png", b"new png")

    write_epub_metadata(epub, edited(), cover={"image/jpeg": JPEG, "image/png": png})

    assert set(dict(entries(epub))) == names  # in place, nothing added
    assert dict(entries(epub))["cover.png"] == b"new png"


def test_an_empty_cover_marker_does_not_count(tmp_path):
    opf = EPUB2_OPF.replace(
        '<meta name="cover" content="cover-img"/>',
        '<meta name="cover" content=""/>\n    <meta name="cover" content="cover-img"/>',
    )
    epub = make_epub(tmp_path / "b.epub", opf)
    names = set(dict(entries(epub)))

    write_epub_metadata(epub, edited(), cover=JPEG)

    assert set(dict(entries(epub))) == names and dict(entries(epub))["cover.jpg"] == JPEG.data


def test_a_marker_inside_legacy_x_metadata_counts_for_reader_and_writer(tmp_path):
    opf = EPUB2_OPF.replace(
        '    <meta name="cover" content="cover-img"/>\n',
        '    <x-metadata><meta name="cover" content="cover-img"/></x-metadata>\n',
    )
    epub = make_epub(tmp_path / "b.epub", opf)
    names = set(dict(entries(epub)))
    assert parse_epub(epub).cover_image.data == b"\xff\xd8 not really a jpeg"  # reader sees it

    used = write_epub_metadata(epub, edited(), cover=JPEG)

    assert used is JPEG
    assert set(dict(entries(epub))) == names and dict(entries(epub))["cover.jpg"] == JPEG.data


def test_rewritten_cover_page_keeps_end_tags_of_empty_elements(tmp_path):
    page = TITLEPAGE.replace("<title>Cover</title>", "<title></title>").replace(
        "<body><div>", '<body><div class="spacer"></div><br/><div>'
    )
    epub = make_epub(
        tmp_path / "b.epub", _with_page(page), extra={"titlepage.xhtml": page.encode()}
    )

    write_epub_metadata(epub, edited(), cover=CoverImage("image/jpeg", b"\xff\xd8 new", 800, 1200))

    written = dict(entries(epub))["titlepage.xhtml"].decode()
    assert 'viewBox="0 0 800 1200"' in written
    assert "<title></title>" in written and '<div class="spacer"></div>' in written
    assert "<br/>" in written  # void elements may stay short


def test_pixel_sizes_with_px_follow_the_new_box(tmp_path):
    page = TITLEPAGE.replace(
        '<image width="325" height="500"', '<image width="325px" height="500px"'
    )
    epub = make_epub(
        tmp_path / "b.epub", _with_page(page), extra={"titlepage.xhtml": page.encode()}
    )

    write_epub_metadata(epub, edited(), cover=CoverImage("image/jpeg", b"\xff\xd8 new", 800, 1200))

    written = dict(entries(epub))["titlepage.xhtml"].decode()
    assert 'viewBox="0 0 800 1200"' in written and 'width="800px" height="1200px"' in written


def test_a_cover_page_in_other_units_is_left_alone(tmp_path):
    page = TITLEPAGE.replace('<image width="325" height="500"', '<image width="9cm" height="14cm"')
    epub = make_epub(
        tmp_path / "b.epub", _with_page(page), extra={"titlepage.xhtml": page.encode()}
    )

    write_epub_metadata(epub, edited(), cover=CoverImage("image/jpeg", b"\xff\xd8 new", 800, 1200))

    assert dict(entries(epub))["titlepage.xhtml"] == page.encode()
