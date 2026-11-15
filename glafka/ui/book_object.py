from enum import Enum

from gi.repository import GObject

from glafka.core.models import Book


class Badge(Enum):
    READER = "reader"  # found on a USB reader at its last check
    EMAILED = "emailed"  # emailed, not found on a reader since


class BookObject(GObject.Object):
    """List-model item wrapping a Book."""

    __gtype_name__ = "GlafkaBookObject"
    # GTK rebinds a tile only when the item object changes; in-place edits must emit "changed"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, book: Book, badge: Badge | None = None) -> None:
        super().__init__()
        self.book = book
        self.badge = badge

    def update(self, book: Book) -> None:
        self.book = book
        self.emit("changed")

    @property
    def formats(self) -> list[str]:
        return [f.format.upper() for f in self.book.files]
