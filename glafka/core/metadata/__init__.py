import logging
import math
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_MAX_SERIES_INDEX = 2**31 - 1


@dataclass
class CoverImage:
    mime_type: str
    data: bytes


@dataclass
class ExtractedMetadata:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    series: str | None = None
    series_index: int | None = None
    description: str | None = None
    language: str | None = None
    tags: list[str] = field(default_factory=list)
    cover_image: CoverImage | None = None

    def primary_author(self) -> str:
        if self.authors and self.authors[0].strip():
            return self.authors[0].strip()
        return "Unknown Author"


def parse_series_index(raw: str) -> int | None:
    """OPF stores floats ("1.0", "1.5"); the app keeps whole numbers, truncating (1.5 -> 1)."""
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0 or value > _MAX_SERIES_INDEX:
        return None
    if not value.is_integer():
        log.warning("Fractional series index %s truncated to %d", value, int(value))
    return int(value)
