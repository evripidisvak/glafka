from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class BookFile:
    id: str
    book_id: str
    format: str
    file_path: str
    file_size: int
    sha256: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class Book:
    id: str
    title: str
    author: str
    series: str | None = None
    series_index: int | None = None
    description: str | None = None
    language: str | None = None
    cover_path: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    files: list[BookFile] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class Destination:
    """A way to send books to an e-reader: a USB drive (known by its ID) or an email address."""

    id: str
    name: str
    kind: str = "usb"  # "usb" or "email"
    volume_uuid: str | None = None
    books_folder: str = "Books"  # USB only: on the device, relative to its root
    formats: list[str] = field(default_factory=lambda: ["epub", "pdf"])  # preferred first
    created_at: datetime = field(default_factory=utc_now)
    email: str | None = None  # email only, e.g. a Send-to-PocketBook address
    last_checked: datetime | None = None  # USB only: last scan of the drive


@dataclass
class EmailAccount:
    """The account books are emailed from. Its password lives in the keyring, never here."""

    address: str
    host: str
    port: int
    security: str = "starttls"  # or "ssl"; never unencrypted
    username: str = ""
    provider: str = "custom"  # or "gmail"

    @classmethod
    def gmail(cls, address: str) -> "EmailAccount":
        return cls(address, "smtp.gmail.com", 587, "starttls", address, "gmail")


@dataclass
class SentRecord:
    id: str
    book_id: str
    target_type: str  # "usb" or "email"
    target_name: str
    format: str
    sent_at: datetime
    status: str
    destination_id: str | None = None  # None in rows from before schema v8
    sha256: str | None = None  # of the copy sent, to recognise it on the reader later
    file_size: int | None = None


@dataclass
class DeviceCopy:
    """A library book found on (or copied to) a USB destination's drive."""

    destination_id: str
    book_id: str
    path: str  # relative to the drive's root, "/"-separated
    seen_at: datetime = field(default_factory=utc_now)


@dataclass
class BookFilter:
    query: str | None = None
    author: str | None = None
    series: str | None = None
    tag: str | None = None
    on_reader: bool = False  # on a USB reader at its last check
    recently_added: bool = False


class BookSort(Enum):
    TITLE_ASC = auto()
    TITLE_DESC = auto()
    AUTHOR_ASC = auto()
    AUTHOR_DESC = auto()
    DATE_ADDED_DESC = auto()
    DATE_ADDED_ASC = auto()
    SERIES_INDEX_ASC = auto()
