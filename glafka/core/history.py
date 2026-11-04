"""What Glafka did with which book and destination, and when."""

import uuid

from glafka.core.db import Database
from glafka.core.models import Book, BookFile, Destination, HistoryAction, HistoryEntry, utc_now


def record(
    db: Database,
    book: Book,
    destination: Destination,
    action: HistoryAction,
    fmt: str | None = None,
    file: BookFile | None = None,
) -> None:
    """Title, author and destination name are copied: the entry outlives renames and deletes."""
    db.log(
        HistoryEntry(
            id=str(uuid.uuid4()),
            book_id=book.id,
            book_title=book.title,
            book_author=book.author,
            action=action,
            destination_id=destination.id,
            destination_kind=destination.kind,
            destination_name=destination.name,
            format=file.format if file is not None else fmt,
            at=utc_now(),
            sha256=file.sha256 if file is not None else None,
            file_size=file.file_size if file is not None else None,
        )
    )
