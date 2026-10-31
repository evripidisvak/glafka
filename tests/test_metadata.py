from glafka.core.metadata.epub import parse_opf

EPUB2_OPF = """<?xml version="1.0" encoding="utf-8"?>
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
</package>"""

EPUB3_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>The Trespasser</dc:title>
    <dc:creator>Tana French</dc:creator>
    <meta property="belongs-to-collection" id="c01">Dublin Murder Squad</meta>
    <meta refines="#c01" property="collection-type">series</meta>
    <meta refines="#c01" property="group-position">6</meta>
  </metadata>
  <manifest>
    <item id="img" href="cover.png" media-type="image/png" properties="cover-image"/>
  </manifest>
</package>"""


def test_parse_opf_epub2():
    meta, cover_href = parse_opf(EPUB2_OPF)
    assert meta.title == "Foundation"
    assert meta.authors == ["Isaac Asimov"]
    assert meta.series == "Foundation"
    assert meta.series_index == 1
    assert meta.description == "The classic saga"
    assert meta.language == "en"
    assert meta.tags == ["Science Fiction"]
    assert cover_href == "images/cover.jpg"


def test_parse_opf_epub3_series_from_element_text():
    meta, cover_href = parse_opf(EPUB3_OPF)
    assert meta.series == "Dublin Murder Squad"
    assert meta.series_index == 6
    assert cover_href == "cover.png"
