"""Books on a USB reader. Every test uses a temporary folder as the reader's drive."""

import shutil
from pathlib import Path

import pytest
from test_importer import make_epub

from glafka.core.errors import GlafkaError
from glafka.core.library import LibraryManager
from glafka.core.models import Destination


@pytest.fixture
def reader(tmp_path):
    """A drive laid out like the user's PocketBook, plus files Glafka must never change."""
    root = tmp_path / "PB710"
    for folder in ("Books", "SendToPb", "system/config", ".Trash-1000/files"):
        (root / folder).mkdir(parents=True)
    (root / "metadata.calibre").write_text("[]")
    return root


@pytest.fixture
def library(tmp_path):
    library = LibraryManager.open(tmp_path / "Library")
    library.add_destination(Destination(id="usb", name="Era Lite", volume_uuid="1A2B-3C4D"))
    return library


def era_lite(library):
    return next(d for d in library.get_destinations() if d.id == "usb")


def add_book(library, tmp_path, title, author="Tana French"):
    return library.import_file(make_epub(tmp_path / f"{title}.epub", title, author)).book


def test_scan_finds_sent_emailed_and_calibre_copies(tmp_path, library, reader):
    sent = add_book(library, tmp_path, "Broken Harbour")
    emailed = add_book(library, tmp_path, "The Trespasser")
    by_calibre = add_book(library, tmp_path, "The Secret Place")
    library.send_to_usb(sent.id, era_lite(library), reader)
    shutil.copy(emailed.files[0].file_path, reader / "SendToPb/The Trespasser - Tana French.epub")
    # Calibre rewrites the file it sends: other bytes, same title and author (two creators)
    calibre = reader / "Books/French, Tana/The Secret Place - Tana French.epub"
    calibre.parent.mkdir(exist_ok=True)
    make_epub(calibre, "The Secret Place", "Tana French", "<dc:creator>Someone Else</dc:creator>")
    make_epub(reader / "SendToPb/Crime and Punishment.epub", "Crime and Punishment", "Dostoyevsky")

    result = library.scan_device(era_lite(library), reader)

    assert result.book_ids == {sent.id, emailed.id, by_calibre.id}
    assert result.other_files == 1
    assert library.books_on_readers() == {sent.id, emailed.id, by_calibre.id}
    assert [c.path for c in library.device_copies(emailed.id)] == [
        "SendToPb/The Trespasser - Tana French.epub"
    ]
    assert era_lite(library).last_checked is not None


def test_scan_ignores_system_and_hidden_folders_and_other_formats(tmp_path, library, reader):
    book = add_book(library, tmp_path, "Broken Harbour")
    shutil.copy(book.files[0].file_path, reader / "system/config/x.epub")
    shutil.copy(book.files[0].file_path, reader / ".Trash-1000/files/x.epub")
    pdf_only = tmp_path / "Tana French - In the Woods.pdf"
    pdf_only.write_bytes(b"%PDF-1.4 not an epub")
    library.import_file(pdf_only)
    make_epub(reader / "Books/In the Woods.epub", "In the Woods", "Tana French")

    result = library.scan_device(era_lite(library), reader)

    assert result.copies == []  # the EPUB doesn't mark the PDF-only book
    assert result.other_files == 1


def test_rescan_drops_books_deleted_on_the_reader(tmp_path, library, reader):
    book = add_book(library, tmp_path, "Broken Harbour")
    copy = library.send_to_usb(book.id, era_lite(library), reader).device_path
    copy.unlink()

    library.scan_device(era_lite(library), reader)

    assert library.books_on_readers() == set()


def test_delete_removes_books_folder_copy_only(tmp_path, library, reader):
    book = add_book(library, tmp_path, "Broken Harbour")
    copy = library.send_to_usb(book.id, era_lite(library), reader).device_path
    emailed = reader / "SendToPb/Broken Harbour - Tana French.epub"
    shutil.copy(book.files[0].file_path, emailed)
    library.scan_device(era_lite(library), reader)

    result = library.remove_from_device(book.id, era_lite(library), reader)

    assert result.removed == ["Books/French, Tana/Broken Harbour - Tana French.epub"]
    assert result.kept == ["SendToPb/Broken Harbour - Tana French.epub"]
    assert not copy.exists() and not copy.parent.exists()  # emptied author folder goes too
    assert (reader / "Books").is_dir()  # the books folder itself stays
    assert emailed.exists()
    assert (reader / "metadata.calibre").read_text() == "[]"
    assert library.books_on_readers() == {book.id}  # still in SendToPb


def test_delete_never_leaves_the_books_folder(tmp_path, library, reader):
    book = add_book(library, tmp_path, "Broken Harbour")
    outside = reader / "Notes.epub"
    shutil.copy(book.files[0].file_path, outside)
    library.scan_device(era_lite(library), reader)
    sneaky = Destination(id="usb", name="Era Lite", books_folder="Books/..")

    with pytest.raises(GlafkaError, match="not on the reader"):
        library.remove_from_device(book.id, sneaky, reader)
    result = library.remove_from_device(book.id, era_lite(library), reader)

    assert result.kept == ["Notes.epub"] and outside.exists()


def test_mark_as_removed_and_cascades(tmp_path, library, reader):
    book = add_book(library, tmp_path, "Broken Harbour")
    other = add_book(library, tmp_path, "The Trespasser")
    library.send_to_usb(book.id, era_lite(library), reader)
    library.send_to_usb(other.id, era_lite(library), reader)

    library.forget_device_copies("usb", book.id)
    assert library.books_on_readers() == {other.id}

    library.delete_book(other.id)
    assert library.books_on_readers() == set()
    library.send_to_usb(book.id, era_lite(library), reader)
    library.delete_destination("usb")
    assert library.books_on_readers() == set()


def test_unplugged_reader_is_a_clean_error(tmp_path, library):
    with pytest.raises(GlafkaError, match="not connected"):
        library.scan_device(era_lite(library), tmp_path / "gone")


def test_history_remembers_the_copy_sent(tmp_path, library, reader):
    book = add_book(library, tmp_path, "Broken Harbour")
    library.send_to_usb(book.id, era_lite(library), reader)

    [record] = library.sends_for_book(book.id)

    assert (record.destination_id, record.sha256) == ("usb", book.files[0].sha256)
    assert Path(book.files[0].file_path).stat().st_size == record.file_size
