from glafka.core.grouping import GroupBy, group_books, sort_key
from glafka.core.models import Book, BookSort


def book(title, author="A", series=None, index=None, tags=()):
    return Book(
        id=title, title=title, author=author, series=series, series_index=index, tags=list(tags)
    )


def summary(groups):
    return [(g.name, [b.title for b in g.books]) for g in groups]


def test_series_groups_ordered_by_index_unindexed_last():
    books = [
        book("Trespasser", series="Dublin Murder Squad", index=6),
        book("Faithful Place", series="Dublin Murder Squad", index=3),
        book("Extra Story", series="Dublin Murder Squad"),
        book("Hyperion", series="Hyperion Cantos", index=1),
        book("Standalone"),
    ]

    groups = group_books(books, GroupBy.SERIES)

    assert summary(groups) == [
        ("Dublin Murder Squad", ["Faithful Place", "Trespasser", "Extra Story"]),
        ("Hyperion Cantos", ["Hyperion"]),
    ]


def test_group_names_match_case_insensitively():
    books = [
        book("B", series="Dublin Murder Squad", index=2),
        book("A", series="dublin murder squad", index=1),
    ]

    groups = group_books(books, GroupBy.SERIES)

    assert summary(groups) == [("Dublin Murder Squad", ["A", "B"])]  # first spelling seen


def test_book_appears_under_each_of_its_tags():
    books = [
        book("Dune", tags=["Sci-Fi", "Classic"]),
        book("Hyperion", tags=["sci-fi"]),
        book("Untagged"),
    ]

    groups = group_books(books, GroupBy.TAG)

    assert summary(groups) == [("Classic", ["Dune"]), ("Sci-Fi", ["Dune", "Hyperion"])]


def test_authors_sorted_with_books_by_title():
    books = [book("Zeta", "Tana French"), book("Alpha", "Dan Simmons"), book("Beta", "Tana French")]

    groups = group_books(books, GroupBy.AUTHOR)

    assert summary(groups) == [("Dan Simmons", ["Alpha"]), ("Tana French", ["Beta", "Zeta"])]


def test_sort_key_orders_and_breaks_ties_by_title():
    from datetime import UTC, datetime

    older = book("Zeta", "Dan Simmons")
    older.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    newer = book("Alpha", "Dan Simmons")
    newer.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    other = book("Beta", "Tana French")
    other.created_at = datetime(2022, 1, 1, tzinfo=UTC)

    def order(sort):
        key, reverse = sort_key(sort)
        return [b.title for b in sorted([older, newer, other], key=key, reverse=reverse)]

    assert order(BookSort.TITLE_ASC) == ["Alpha", "Beta", "Zeta"]
    assert order(BookSort.TITLE_DESC) == ["Zeta", "Beta", "Alpha"]
    assert order(BookSort.AUTHOR_ASC) == ["Alpha", "Zeta", "Beta"]  # same author: by title
    assert order(BookSort.DATE_ADDED_DESC) == ["Alpha", "Beta", "Zeta"]


def test_sort_applies_inside_sections_but_not_to_series():
    books = [
        book("Alpha", "Tana French", series="S", index=2),
        book("Zeta", "Tana French", series="S", index=1),
    ]

    by_author = group_books(books, GroupBy.AUTHOR, BookSort.TITLE_DESC)
    by_series = group_books(books, GroupBy.SERIES, BookSort.TITLE_DESC)

    assert summary(by_author) == [("Tana French", ["Zeta", "Alpha"])]
    assert summary(by_series) == [("S", ["Zeta", "Alpha"])]  # reading order: #1 then #2
