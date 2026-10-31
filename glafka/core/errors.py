import sqlite3


class GlafkaError(Exception):
    """Base class for errors the UI should show to the user."""


class MetadataError(GlafkaError):
    """Ebook metadata could not be read."""


class NotFoundError(GlafkaError):
    """A file or record does not exist."""


# Expected failures of library operations (bad file, disk, database). Anything else is a bug.
RECOVERABLE_ERRORS = (GlafkaError, OSError, sqlite3.Error)
