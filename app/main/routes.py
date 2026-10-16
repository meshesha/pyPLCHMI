"""Home page views: screen list, landing redirect and the Restricted Area page."""

from flask import make_response, redirect, render_template, request, url_for
from flask_login import current_user

from app.main import bp
from app.models import PLC, Dashboard, Perm
from app.utils.tables import ListPagination, parse_state

#: Cookie remembering the chosen view of the screen list (``cards`` / ``table``).
VIEW_COOKIE = "hmi_screens_view"
VIEWS = ("cards", "table")

#: Sort keys of the screen table -> function(screen) giving the sort value.
SCREEN_SORT = {
    "name": lambda d: d.name.lower(),
    "kind": lambda d: (d.kind, d.name.lower()),
    "tags": lambda d: (len(d.datapoints), d.name.lower()),
    "plcs": lambda d: (", ".join(sorted(p.name.lower() for p in d.plcs)), d.name.lower()),
    "access": lambda d: (not d.is_public, d.name.lower()),
    "created": lambda d: d.created_at.timestamp() if d.created_at else 0.0,
}
SCREEN_FILTERS = ("kind", "access", "plc")


def landing_url() -> str:
    """URL where the current visitor should land.

    * Logged-in users: their home screen (if set and accessible), else the
      screen list.
    * Visitors: the public home screen (if configured), else the login page.

    Returns:
        A URL string.
    """
    screen = current_user.landing_screen
    if screen is not None:
        return url_for("dashboards.view", slug=screen.slug)
    if current_user.is_authenticated:
        return url_for("main.screens")
    return url_for("auth.login")


@bp.route("/")
def index():
    """Entry point: send the visitor to their landing screen.

    Returns:
        Redirect (see :func:`landing_url`).
    """
    return redirect(landing_url())


def screen_matches(screen: Dashboard, q: str, filters: dict) -> bool:
    """Check a screen against the search text and the filters of the screen list.

    Args:
        screen: The screen.
        q: Search text (matched case-insensitively against name, description,
            URL name and PLC names); empty matches everything.
        filters: Active filters: ``kind`` (``grid``/``image``), ``access``
            (``public``/``private``) and ``plc`` (PLC id as text).

    Returns:
        ``True`` if the screen is shown.
    """
    if q:
        text = " ".join([screen.name, screen.description or "", screen.slug] + [p.name for p in screen.plcs])
        if q.lower() not in text.lower():
            return False
    if filters.get("kind") and screen.kind != filters["kind"]:
        return False
    if filters.get("access") == "public" and not screen.is_public:
        return False
    if filters.get("access") == "private" and screen.is_public:
        return False
    if filters.get("plc") and filters["plc"] not in {str(p.id) for p in screen.plcs}:
        return False
    return True


def chosen_view() -> str:
    """The view of the screen list: ``?view=`` if given, else the remembered one.

    Returns:
        ``"cards"`` (default) or ``"table"``.
    """
    view = request.args.get("view") or request.cookies.get(VIEW_COOKIE, "cards")
    return view if view in VIEWS else "cards"


@bp.route("/screens/")
def screens():
    """List the screens the current user may open, as cards or as a table.

    Both views share the search box and the filters (type, access, PLC); the
    table view adds sorting by column and pages.  The chosen view is
    remembered in a cookie.  Visitors (not logged in) see the public screens
    when public access is on, otherwise they are sent to the login page.

    Returns:
        The rendered screen list or a redirect.
    """
    all_screens = Dashboard.query.order_by(Dashboard.name).all()
    visible = [s for s in all_screens if current_user.can_view_dashboard(s)]
    if not current_user.is_authenticated and not visible:
        return redirect(url_for("auth.login"))
    view = chosen_view()
    state = parse_state(request.args, SCREEN_SORT, "name", filter_names=SCREEN_FILTERS)
    rows = [s for s in visible if screen_matches(s, state.q, state.filters)]
    rows.sort(key=SCREEN_SORT[state.sort], reverse=state.direction == "desc")
    pagination = ListPagination(rows, state.page, state.per_page)
    state.page = pagination.page
    plc_ids = {p.id for s in visible for p in s.plcs}
    plc_options = [(p.id, p.name) for p in PLC.query.order_by(PLC.name) if p.id in plc_ids]
    plc_count = PLC.query.count() if current_user.can(Perm.MANAGE_PLCS) else None
    home = current_user.landing_screen
    html = render_template("main/index.html", dashboards=rows, total=len(visible), plc_count=plc_count, home=home,
                           view=view, state=state, pagination=pagination, plc_options=plc_options)
    response = make_response(html)
    if request.args.get("view") in VIEWS:
        response.set_cookie(VIEW_COOKIE, view, max_age=365 * 24 * 3600, samesite="Lax", httponly=True)
    return response


@bp.route("/restricted")
def restricted():
    """Show the Restricted Area page directly (used as a link target).

    Returns:
        The page with status 403.
    """
    return render_template("errors/restricted.html", screen=None), 403
