"""Plain text from the HTML that ebook descriptions often are (Calibre writes them that way)."""

import re
from html.parser import HTMLParser

_BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr", "ul", "ol"}
_LOOKS_LIKE_HTML = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*\b[^<>]*>")  # "<p", not "< sign"


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # &amp; and &#8217; become characters
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "br":
            self.parts.append("\n")
        elif tag in _BLOCKS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCKS:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(re.sub(r"\s+", " ", data))  # HTML whitespace collapses


def html_to_text(text: str) -> str:
    """Readable plain text; text without tags is returned as it is."""
    if not _LOOKS_LIKE_HTML.search(text):
        return text
    collector = _TextCollector()
    collector.feed(text)
    collector.close()
    joined = "".join(collector.parts)
    lines = [line.strip() for line in joined.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
