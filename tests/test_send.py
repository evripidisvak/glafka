"""Sending to a USB reader. Every test uses a temporary folder as the device."""

import shutil
from collections import namedtuple
from pathlib import Path, PurePosixPath

import pytest
from test_importer import make_epub

from glafka.core.errors import GlafkaError
from glafka.core.importer import compute_sha256
from glafka.core.library import LibraryManager
from glafka.core.models import Book, Destination
from glafka.core.send import SendStatus, author_sort, device_path, fat_name


def test_paths_match_what_calibre_put_on_the_users_reader():
    # Real files on the user's PocketBook, sent by Calibre
    assert device_path(Book("1", "Butter", "Asako Yuzuki"), "epub", "Books") == PurePosixPath(
        "Books/Yuzuki, Asako/Butter - Asako Yuzuki.epub"
    )
    assert device_path(
        Book("2", "Modern Testing with PHP", "Mauro Chojrin"), "epub", "Books"
    ) == PurePosixPath("Books/Chojrin, Mauro/Modern Testing with PHP - Mauro Chojrin.epub")


@pytest.mark.parametrize(
    ("authors", "expected"),
    [
        ("Asako Yuzuki", "Yuzuki, Asako"),
        ("Ben Auffarth & Leonid Kuligin", "Auffarth, Ben & Kuligin, Leonid"),
        ("Yuzuki, Asako", "Yuzuki, Asako"),  # already sorted
        ("Plato", "Plato"),
        ("Martin Luther King Jr.", "King, Martin Luther, Jr."),
        ("Unknown Author", "Unknown"),  # Calibre's folder for books without an author
    ],
)
def test_author_sort(authors, expected):
    assert author_sort(authors) == expected


def test_fat_names_drop_forbidden_characters_and_trailing_dots():
    assert fat_name('What If?: "Serious" Answers...', 100) == "What If__ _Serious_ Answers"
    assert device_path(Book("1", "Title", "Unknown Author"), "pdf", "Books") == PurePosixPath(
        "Books/Unknown/Title - Unknown.pdf"
    )


Setup = namedtuple("Setup", "library book destination device")


@pytest.fixture
def setup(tmp_path):
    library = LibraryManager.open(tmp_path / "Library")
    book = library.import_file(make_epub(tmp_path / "b.epub", "Butter", "Asako Yuzuki")).book
    destination = Destination(id="r", name="Era Lite", volume_uuid="1A2B-3C4D")
    library.add_destination(destination)
    device = tmp_path / "PB710"
    (device / "Books").mkdir(parents=True)
    return Setup(library, book, destination, device)


def on_device(s: Setup) -> Path:
    return s.device / "Books" / "Yuzuki, Asako" / "Butter - Asako Yuzuki.epub"


def test_send_copies_in_calibre_layout_and_records_it(setup):
    result = setup.library.send_to_usb(setup.book.id, setup.destination, setup.device)

    assert result.status is SendStatus.SENT
    assert result.device_path == on_device(setup)
    assert compute_sha256(on_device(setup)) == setup.book.files[0].sha256
    assert setup.library.books_on_readers() == {setup.book.id}
    assert not list(on_device(setup).parent.glob("*.part"))


def test_identical_file_already_there(setup):
    setup.library.send_to_usb(setup.book.id, setup.destination, setup.device)

    again = setup.library.send_to_usb(setup.book.id, setup.destination, setup.device)

    assert again.status is SendStatus.ALREADY_THERE


def test_different_copy_is_left_alone_unless_replace(setup):
    on_device(setup).parent.mkdir(parents=True)
    on_device(setup).write_bytes(b"another edition, sent by Calibre")

    result = setup.library.send_to_usb(setup.book.id, setup.destination, setup.device)

    assert result.status is SendStatus.DIFFERENT_COPY_THERE
    assert on_device(setup).read_bytes() == b"another edition, sent by Calibre"
    assert setup.library.books_on_readers() == set()

    replaced = setup.library.send_to_usb(
        setup.book.id, setup.destination, setup.device, replace=True
    )

    assert replaced.status is SendStatus.SENT
    assert compute_sha256(on_device(setup)) == setup.book.files[0].sha256


def test_no_format_the_destination_takes(setup):
    pdf_only = Destination(id="r2", name="PDF Reader", formats=["pdf"])

    result = setup.library.send_to_usb(setup.book.id, pdf_only, setup.device)

    assert result.status is SendStatus.NO_SUITABLE_FORMAT
    assert not (setup.device / "Books" / "Yuzuki, Asako").exists()


def test_unplugged_reader_is_a_clean_error(setup):
    shutil.rmtree(setup.device)

    with pytest.raises(GlafkaError, match="not connected"):
        setup.library.send_to_usb(setup.book.id, setup.destination, setup.device)


def test_full_reader_is_a_clean_error_and_writes_nothing(setup, monkeypatch):
    full = namedtuple("DiskUsage", "total used free")(100, 100, 0)
    monkeypatch.setattr("glafka.core.send.shutil.disk_usage", lambda _path: full)

    with pytest.raises(GlafkaError, match="Not enough space"):
        setup.library.send_to_usb(setup.book.id, setup.destination, setup.device)

    assert not (setup.device / "Books" / "Yuzuki, Asako").exists()
