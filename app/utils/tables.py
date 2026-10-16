"""Server-side table helper: search, filters, sorting and pagination.

Every list page (Tags, Alarms, Event history) builds a SQLAlchemy query and
hands it to :func:`table_page` together with the columns that may be searched
and sorted and the filter functions it supports.  All state lives in the URL
query string (``q``, ``sort``, ``dir``, ``page``, ``per_page`` and filters), so
pages are bookmarkable and the browser's back button works.  The Jinja macros
in ``templates/_table.html`` render the matching controls.
"""

from dataclasses import dataclass, field

from sqlalchemy import or_

#: Page sizes offered in the "rows per page" selector.
PAGE_SIZES = (10, 25, 50, 100)


@dataclass
class TableState:
    """The table parameters parsed from the request.

    Attributes:
        q: Free-text search.
        sort: Sort key (one of the page's sortable keys).
        direction: ``"asc"`` or ``"desc"``.
        page: 1-based page number.
        per_page: Rows per page.
        filters: Active filter values by name (empty values are omitted).
    """

    q: str = ""
    sort: str = ""
    direction: str = "asc"
    page: int = 1
    per_page: int = 25
    filters: dict = field(default_factory=dict)

    def args(self, **overrides) -> dict:
        """Query-string arguments reproducing this state, with overrides.

        Args:
            **overrides: Values to change (``None`` removes a key).

        Returns:
            Dict suitable for ``url_for(endpoint, **args)``.
        """
        data = {"q": self.q, "sort": self.sort, "dir": self.direction, "page": self.page,
                "per_page": self.per_page, **self.filters}
        data.update(overrides)
        return {k: v for k, v in data.items() if v not in (None, "")}

    def sort_args(self, key: str) -> dict:
        """Arguments for clicking a column header (toggles the direction).

        Args:
            key: Sort key of the column.

        Returns:
            Query-string arguments (page reset to 1).
        """
        direction = "desc" if self.sort == key and self.direction == "asc" else "asc"
        return self.args(sort=key, dir=direction, page=1)


def parse_state(args, sortable, default_sort: str, default_dir: str = "asc", filter_names=(),
                default_per_page: int = 25) -> TableState:
    """Read table parameters from ``request.args`` with validation.

    Args:
        args: The request's query arguments.
        sortable: Allowed sort keys.
        default_sort: Sort key used when none/invalid is given.
        default_dir: Default direction.
        filter_names: Names of the supported filters.
        default_per_page: Default page size.

    Returns:
        A :class:`TableState`.
    """
    sort = args.get("sort", default_sort)
    if sort not in sortable:
        sort = default_sort
    direction = args.get("dir", default_dir)
    if direction not in ("asc", "desc"):
        direction = default_dir
    try:
        per_page = int(args.get("per_page", default_per_page))
    except ValueError:
        per_page = default_per_page
    if per_page not in PAGE_SIZES:
        per_page = default_per_page
    try:
        page = max(1, int(args.get("page", 1)))
    except ValueError:
        page = 1
    filters = {name: args.get(name, "").strip() for name in filter_names if args.get(name, "").strip()}
    return TableState(q=args.get("q", "").strip()[:100], sort=sort, direction=direction, page=page,
                      per_page=per_page, filters=filters)


def table_query(query, state: TableState, sort_columns: dict, search_columns=(), filters: dict | None = None):
    """Apply search, filters and sorting to a query (no pagination).

    Used directly by the Excel export of the current search/filter.

    Args:
        query: Base SQLAlchemy query (``Model.query...``).
        state: Parsed :class:`TableState`.
        sort_columns: ``sort key -> column expression``.
        search_columns: Columns searched (case-insensitive ``LIKE``) for ``q``.
        filters: ``filter name -> function(query, value) -> query``.

    Returns:
        The filtered, sorted query.
    """
    if state.q and search_columns:
        like = f"%{state.q}%"
        query = query.filter(or_(*[col.ilike(like) for col in search_columns]))
    for name, value in state.filters.items():
        func = (filters or {}).get(name)
        if func is not None:
            query = func(query, value)
    column = sort_columns[state.sort]
    return query.order_by(column.desc() if state.direction == "desc" else column.asc())


def table_page(query, state: TableState, sort_columns: dict, search_columns=(), filters: dict | None = None):
    """Apply search, filters, sorting and pagination to a query.

    Args:
        query: Base SQLAlchemy query (``Model.query...``).
        state: Parsed :class:`TableState`.
        sort_columns: ``sort key -> column expression``.
        search_columns: Columns searched (case-insensitive ``LIKE``) for ``q``.
        filters: ``filter name -> function(query, value) -> query``.

    Returns:
        A Flask-SQLAlchemy ``Pagination`` object (``items``, ``total``,
        ``pages``, ``page``...).  Out-of-range pages fall back to the last page.
    """
    query = table_query(query, state, sort_columns, search_columns, filters)
    pagination = query.paginate(page=state.page, per_page=state.per_page, error_out=False)
    if pagination.pages and state.page > pagination.pages:
        state.page = pagination.pages
        pagination = query.paginate(page=state.page, per_page=state.per_page, error_out=False)
    return pagination


class ListPagination:
    """Pagination of a Python list, with the interface of Flask-SQLAlchemy's ``Pagination``.

    Used where rows are filtered in Python (e.g. the screen list, which
    depends on per-screen access rules) so the same ``pager`` macro works.

    Attributes:
        items: The rows of the current page.
        total: Number of rows in all pages.
        page: Current 1-based page (clamped to the last page).
        per_page: Rows per page.
    """

    def __init__(self, rows: list, page: int, per_page: int):
        """Cut one page out of ``rows``.

        Args:
            rows: All rows (already filtered and sorted).
            page: Requested 1-based page; out-of-range pages fall back to the last page.
            per_page: Rows per page.
        """
        self.total = len(rows)
        self.per_page = per_page
        self.page = min(max(1, page), self.pages or 1)
        start = (self.page - 1) * per_page
        self.items = rows[start:start + per_page]

    @property
    def pages(self) -> int:
        """Number of pages (0 when there are no rows)."""
        return -(-self.total // self.per_page) if self.per_page else 0

    @property
    def has_prev(self) -> bool:
        """``True`` if there is a page before this one."""
        return self.page > 1

    @property
    def has_next(self) -> bool:
        """``True`` if there is a page after this one."""
        return self.page < self.pages

    @property
    def prev_num(self) -> int | None:
        """Number of the previous page, or ``None``."""
        return self.page - 1 if self.has_prev else None

    @property
    def next_num(self) -> int | None:
        """Number of the next page, or ``None``."""
        return self.page + 1 if self.has_next else None

    def iter_pages(self, left_edge: int = 2, left_current: int = 2, right_current: int = 4, right_edge: int = 2):
        """Page numbers for a pager, with ``None`` where pages are skipped.

        Same rules as Flask-SQLAlchemy's ``Pagination.iter_pages``.

        Args:
            left_edge: Pages shown at the start.
            left_current: Pages shown before the current page.
            right_current: Pages shown after the current page.
            right_edge: Pages shown at the end.

        Yields:
            Page numbers or ``None`` for a gap.
        """
        last = 0
        for num in range(1, self.pages + 1):
            if (num <= left_edge or self.page - left_current <= num <= self.page + right_current
                    or num > self.pages - right_edge):
                if last + 1 != num:
                    yield None
                yield num
                last = num
