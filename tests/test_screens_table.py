"""Tests for the screen list: card and table views, search, filters, sorting and pages."""

from app.models import Dashboard
from app.utils.tables import ListPagination


def make_screens(db, count: int, **kwargs) -> None:
    """Add ``count`` grid screens named ``Screen 01``, ``Screen 02``…"""
    for i in range(1, count + 1):
        db.session.add(Dashboard(name=f"Screen {i:02d}", slug=f"screen-{i:02d}", **kwargs))
    db.session.commit()


def names(html: str) -> list[str]:
    """Names of the screens in the table, in order."""
    body = html.split('id="screens-table"')[1].split("</table>")[0]
    links = body.split('class="fw-semibold" href="')[1:]
    return [link.split('">', 1)[1].split("</a>")[0] for link in links]


def test_list_pagination():
    """The list paginator behaves like Flask-SQLAlchemy's pagination."""
    p = ListPagination(list(range(95)), 20, 10)
    assert (p.page, p.pages, p.items[0], p.has_next, p.prev_num) == (10, 10, 90, False, 9)
    assert list(p.iter_pages(1, 2, 2, 1)) == [1, None, 8, 9, 10]
    empty = ListPagination([], 3, 10)
    assert (empty.page, empty.pages, empty.items, list(empty.iter_pages())) == (1, 0, [], [])


def test_cards_by_default_and_table_remembered(client, login_as, dashboards):
    """Cards are the default; the table view is chosen with ?view=table and remembered."""
    login_as("admin")
    html = client.get("/screens/").get_data(as_text=True)
    assert 'id="screens-table"' not in html and 'id="view-table"' in html
    resp = client.get("/screens/?view=table")
    assert 'id="screens-table"' in resp.get_data(as_text=True)
    assert "hmi_screens_view=table" in resp.headers["Set-Cookie"]
    assert 'id="screens-table"' in client.get("/screens/").get_data(as_text=True)
    client.get("/screens/?view=cards")
    assert 'id="screens-table"' not in client.get("/screens/").get_data(as_text=True)
    assert 'id="screens-table"' not in client.get("/screens/?view=bogus").get_data(as_text=True)


def test_search_filter_sort(client, login_as, dashboards):
    """Search, filters and column sorting apply to the table (and to the cards)."""
    login_as("admin")
    assert names(client.get("/screens/?view=table").get_data(as_text=True)) == ["Private", "Public"]
    assert names(client.get("/screens/?view=table&sort=name&dir=desc").get_data(as_text=True)) == ["Public", "Private"]
    assert names(client.get("/screens/?view=table&access=public").get_data(as_text=True)) == ["Public"]
    assert names(client.get("/screens/?view=table&access=private").get_data(as_text=True)) == ["Private"]
    assert names(client.get("/screens/?view=table&q=PUBL").get_data(as_text=True)) == ["Public"]
    assert names(client.get("/screens/?view=table&kind=image").get_data(as_text=True)) == []
    plc_id = dashboards["private"].plcs[0].id
    assert "Private" in names(client.get(f"/screens/?view=table&plc={plc_id}").get_data(as_text=True))
    cards = client.get("/screens/?view=cards&q=publ").get_data(as_text=True)
    assert ">Public</a>" in cards and ">Private</a>" not in cards
    assert "No screens match the search." in client.get("/screens/?view=cards&q=zzz").get_data(as_text=True)
    assert client.get("/screens/?view=table&sort=evil&dir=sideways").status_code == 200


def test_pages(client, login_as, db, users):
    """Long lists are split into pages; an out-of-range page shows the last page."""
    make_screens(db, 30)
    login_as("admin")
    first = client.get("/screens/?view=table&per_page=10").get_data(as_text=True)
    assert names(first)[0] == "Screen 01" and len(names(first)) == 10 and "1–10 of 30" in first
    last = client.get("/screens/?view=table&per_page=10&page=99").get_data(as_text=True)
    assert names(last)[-1] == "Screen 30" and "21–30 of 30" in last


def test_table_respects_screen_access(client, login_as, dashboards):
    """The table lists only the screens the user may open; actions only for editors."""
    login_as("viewer")
    html = client.get("/screens/?view=table").get_data(as_text=True)
    assert 'id="screens-table"' in html and "/edit" not in html.split('id="screens-table"')[1]
