"""Regression tests: a slow request with an old cookie must not undo a login ("logged out by itself")."""

from flask import Response, session

from app.extensions import login_manager


def test_anonymous_session_is_not_rewritten(app):
    """A visitor session (language, CSRF token) does not "fail" session protection."""
    with app.test_request_context("/screens/x"):
        session.update({"lang": "en", "csrf_token": "abc"})
        session.modified = False
        assert login_manager._session_protection_failed() is False
        assert session.modified is False and "_remember" not in session


def test_logged_in_session_is_still_protected(app):
    """A logged-in session copied to another browser/IP is still rejected (strong protection)."""
    with app.test_request_context("/screens/x"):
        session.update({"_user_id": "1", "_id": "someone-else", "_fresh": True})
        assert login_manager._session_protection_failed() is True
        assert "_user_id" not in session and session["_remember"] == "clear"


def test_api_polls_never_send_the_session_cookie(app):
    """Read-only API polls do not write the session; forms and pages still do."""
    for path, method, expect in (("/api/dashboards/1/values", "GET", False), ("/history/api/series", "GET", False),
                                 ("/api/datapoints/1/write", "POST", True), ("/screens/x", "GET", True)):
        with app.test_request_context(path, method=method):
            session["lang"] = "he"
            resp = Response()
            app.session_interface.save_session(app, session, resp)
            assert bool(resp.headers.getlist("Set-Cookie")) is expect, path
