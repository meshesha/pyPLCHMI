"""Tests for the image library (upload, validation, serving, delete)."""

import io
import os
import struct
import zlib

from app.assets.storage import AssetError, detect_mimetype
from app.models import Asset, Dashboard

import pytest


def png_bytes(w=4, h=3) -> bytes:
    """Build a tiny valid PNG.

    Args:
        w: Width.
        h: Height.

    Returns:
        PNG file content.
    """
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(tag, data):
        """Encode one PNG chunk."""
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
SAFE_SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


@pytest.mark.parametrize("data,mime", [
    (png_bytes(), "image/png"), (b"\xff\xd8\xff\xe0rest", "image/jpeg"), (GIF, "image/gif"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"), (SAFE_SVG, "image/svg+xml"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml"),
])
def test_detect_mimetype(data, mime):
    """Supported formats are recognised by content."""
    assert detect_mimetype(data) == mime


@pytest.mark.parametrize("data", [
    b"MZ\x90\x00 windows exe", b"<html><body>hi</body></html>",
    b"<svg><script>alert(1)</script></svg>", b'<svg onload="alert(1)"></svg>',
    b'<svg><a href="javascript:alert(1)">x</a></svg>', b"<svg><foreignObject></foreignObject></svg>",
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "y">]><svg></svg>', b"\xff\xfe\x00garbage",
])
def test_reject_unsafe_or_unknown(data):
    """Non-images and unsafe SVGs are refused."""
    with pytest.raises(AssetError):
        detect_mimetype(data)


def upload(client, data, name="pic.png", accept_json=True, **form):
    """POST a file to the upload endpoint.

    Args:
        client: Test client.
        data: File bytes.
        name: File name.
        accept_json: Request a JSON answer.
        **form: Extra form fields.

    Returns:
        The response.
    """
    headers = {"Accept": "application/json"} if accept_json else {}
    return client.post("/assets/upload", data={"file": (io.BytesIO(data), name), **form},
                       content_type="multipart/form-data", headers=headers)


def test_upload_json(client, login_as, app):
    """AJAX uploads return the new asset; the file is stored under a random name."""
    login_as("admin")
    resp = upload(client, png_bytes(), "Chiller unit.png", category="Equipment")
    assert resp.status_code == 200
    a = resp.get_json()["assets"][0]
    assert a["name"] == "Chiller unit" and a["category"] == "Equipment" and a["mimetype"] == "image/png"
    asset = Asset.query.get(a["id"])
    assert os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], asset.filename))
    assert asset.filename != "Chiller unit.png"


def test_upload_form_redirect_and_errors(client, login_as):
    """Form uploads redirect with flash messages; bad files are reported."""
    login_as("admin")
    resp = upload(client, b"not an image", "x.png", accept_json=False)
    assert resp.status_code == 302
    page = client.get("/assets/").data
    assert b"Unsupported file type" in page
    bad = upload(client, b"not an image", "x.png")
    assert bad.status_code == 400 and bad.get_json()["errors"]


def test_upload_too_large(client, login_as, app):
    """Files above ASSET_MAX_BYTES are refused."""
    app.config["ASSET_MAX_BYTES"] = 100
    login_as("admin")
    assert upload(client, png_bytes(40, 40)).status_code == 400


def test_upload_requires_permission(client, login_as):
    """Viewers cannot upload."""
    login_as("viewer")
    assert upload(client, png_bytes()).status_code == 403


def test_serve_file_headers(client, login_as):
    """Files are served with nosniff and a sandboxing CSP; login required."""
    login_as("admin")
    aid = upload(client, SAFE_SVG, "logo.svg").get_json()["assets"][0]["id"]
    resp = client.get(f"/assets/{aid}/file")
    assert resp.status_code == 200 and resp.mimetype == "image/svg+xml"
    assert "sandbox" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    client.post("/auth/logout")
    assert client.get(f"/assets/{aid}/file").status_code == 302  # to login
    login_as("guest")
    assert client.get(f"/assets/{aid}/file").status_code == 200


def test_list_and_rename(client, login_as):
    """Assets are listed as JSON and can be renamed."""
    login_as("admin")
    aid = upload(client, GIF, "spin.gif").get_json()["assets"][0]["id"]
    assert client.get("/assets/list").get_json()[0]["mimetype"] == "image/gif"
    client.post(f"/assets/{aid}/rename", data={"name": "Spinner", "category": "Icons"})
    assert Asset.query.get(aid).name == "Spinner" and Asset.query.get(aid).category == "Icons"


def test_delete_blocked_while_used(client, login_as, db, app):
    """An asset used by a layout cannot be deleted; unused ones can."""
    login_as("admin")
    aid = upload(client, png_bytes()).get_json()["assets"][0]["id"]
    dash = Dashboard(name="Map", slug="map", kind="image")
    dash.set_layout({"version": 1, "canvas": {"backgroundImage": aid}, "elements": []})
    db.session.add(dash)
    db.session.commit()
    resp = client.post(f"/assets/{aid}/delete", headers={"Accept": "application/json"})
    assert resp.status_code == 409 and "Map" in resp.get_json()["error"]
    dash.set_layout({"version": 1, "canvas": {}, "elements": []})
    db.session.commit()
    path = os.path.join(app.config["UPLOAD_FOLDER"], Asset.query.get(aid).filename)
    client.post(f"/assets/{aid}/delete")
    assert Asset.query.get(aid) is None and not os.path.exists(path)


def test_library_page(client, login_as):
    """The library root lists the folders; the folder page lists its images."""
    login_as("admin")
    upload(client, png_bytes(), "pump photo.png")
    root = client.get("/assets/").data.decode()
    assert 'data-drop-folder="Images"' in root and "pump photo" not in root
    html = client.get("/assets/?folder=Images").data.decode()
    assert "pump photo" in html and 'title="Delete"' in html
