from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from glafka.core.models import Book, BookSort


class GroupBy(Enum):
    SERIES = "series"
    AUTHOR = "author"
    TAG = "tag"


@dataclass
class Group:
    name: str
    books: list[Book]


_DESCENDING = {BookSort.TITLE_DESC, BookSort.AUTHOR_DESC, BookSort.DATE_ADDED_DESC}


def sort_key(sort: BookSort) -> tuple[Callable[[Book], tuple], bool]:
    """Key function and `reverse` flag for a sort order; ties fall back to the title."""
    match sort:
        case BookSort.TITLE_ASC | BookSort.TITLE_DESC:
            key = _title_order
        case BookSort.AUTHOR_ASC | BookSort.AUTHOR_DESC:

            def key(b: Book) -> tuple:
                return (b.author.casefold(), *_title_order(b))

        case BookSort.DATE_ADDED_ASC | BookSort.DATE_ADDED_DESC:

            def key(b: Book) -> tuple:
                return (b.created_at, *_title_order(b))

        case BookSort.SERIES_INDEX_ASC:

            def key(b: Book) -> tuple:
                return ((b.series or "").casefold(), *_series_order(b))

    return key, sort in _DESCENDING


def group_books(
    books: Iterable[Book], by: GroupBy, sort: BookSort = BookSort.TITLE_ASC
) -> list[Group]:
    """Sections in display order: groups by name, books in each ordered for that grouping.

    Names match case-insensitively (the first spelling seen is shown). A book appears in
    every group it belongs to (several tags), and in none if it has no series or tags.
    Series sections keep reading order; other sections use `sort`.
    """
    groups: dict[str, Group] = {}
    for book in books:
        for name in _names(book, by):
            name = name.strip()
            if name:
                groups.setdefault(name.casefold(), Group(name, [])).books.append(book)

    key, reverse = (_series_order, False) if by is GroupBy.SERIES else sort_key(sort)
    ordered = [groups[name] for name in sorted(groups)]
    for group in ordered:
        group.books.sort(key=key, reverse=reverse)
    return ordered


def _names(book: Book, by: GroupBy) -> list[str]:
    match by:
        case GroupBy.SERIES:
            return [book.series] if book.series else []
        case GroupBy.AUTHOR:
            return [book.author]
        case GroupBy.TAG:
            return book.tags


def _title_order(book: Book) -> tuple:
    return (book.title.casefold(), book.id)


def _series_order(book: Book) -> tuple:
    # Books without an index go last
    return (book.series_index is None, book.series_index or 0, *_title_order(book))
