from glafka.core.text import html_to_text


def test_calibre_style_description_becomes_paragraphs():
    html = (
        '<div><p class="description">In BROKEN HARBOUR, a ghost estate &amp; more.</p>'
        "<p>Second   paragraph<br/>with a break.</p></div>"
    )

    assert html_to_text(html) == (
        "In BROKEN HARBOUR, a ghost estate & more.\n\nSecond paragraph\nwith a break."
    )


def test_plain_text_is_left_as_it_is():
    text = "Line one\nLine two with a < sign and 3 > 2"

    assert html_to_text(text) == text


def test_entities_and_curly_quotes():
    assert (
        html_to_text("<p>“one of the best” &#8212; The Post</p>") == "“one of the best” — The Post"
    )
