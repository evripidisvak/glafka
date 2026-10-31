from gi.repository import GObject

from glafka.core.models import Book


class BookObject(GObject.Object):
    """List-model item wrapping a Book."""

    __gtype_name__ = "GlafkaBookObject"
    # GTK rebinds a tile only when the item object changes; in-place edits must emit "changed"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, book: Book, on_reader: bool = False) -> None:
        super().__init__()
        self.book = book
        self.on_reader = on_reader  # on a USB reader at its last check: tiles show a badge

    def update(self, book: Book) -> None:
        self.book = book
        self.emit("changed")

    @property
    def formats(self) -> list[str]:
        return [f.format.upper() for f in self.book.files]
