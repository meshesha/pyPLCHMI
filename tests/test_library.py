"""Tests for the information library (SHA-1 de-duplication) and files attached to PLCs."""

import hashlib
import io
import os

from app.documents.storage import detect_type, library_folder
from app.models import InfoFile

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def upload(client, data: bytes, name: str = "manual.pdf", **extra):
    """POST a file to the library API."""
    return client.post("/library/api/files", data={"file": (io.BytesIO(data), name), **extra},
                       content_type="multipart/form-data")


def test_detect_type():
    """Types are recognised by content, not by name."""
    assert detect_type(PDF) == "pdf" and detect_type(PNG) == "png"
    assert detect_type(b"\xff\xd8\xff\xe0abc") == "jpg" and detect_type(b"MZ\x90\x00") is None


def test_upload_stores_sha1_and_refuses_duplicates(client, login_as, app):
    """A new file gets its SHA-1; the same content is refused with the existing file (nothing stored)."""
    login_as("admin")
    resp = upload(client, PDF, "Pump manual.pdf")
    assert resp.status_code == 201
    data = resp.get_json()["file"]
    assert data["sha1"] == hashlib.sha1(PDF).hexdigest() and data["name"] == "Pump manual" and data["kind"] == "pdf"
    item = InfoFile.query.one()
    assert os.path.isfile(os.path.join(library_folder(), item.filename))
    dup = upload(client, PDF, "copy.pdf")
    assert dup.status_code == 409 and dup.get_json()["duplicate"]["id"] == item.id
    assert InfoFile.query.count() == 1 and len(os.listdir(library_folder())) == 1
    check = client.get(f"/library/api/check?sha1={item.sha1.upper()}").get_json()
    assert check["exists"] and check["file"]["name"] == "Pump manual"
    assert client.get("/library/api/check?sha1=" + "0" * 40).get_json() == {"exists": False, "file": None}
    assert client.get("/library/api/check?sha1=xyz").status_code == 400


def test_upload_rejects_other_types(client, login_as):
    """Only PDFs and images are accepted."""
    login_as("admin")
    resp = upload(client, b"MZ\x90\x00 evil", "tool.exe")
    assert resp.status_code == 400 and "Only PDF files and images" in resp.get_json()["error"]
    assert upload(client, b"", "empty.pdf").status_code == 400


def test_open_download_rename_delete(client, login_as):
    """Files open inline or download; they can be renamed and deleted."""
    login_as("admin")
    fid = upload(client, PDF, "Wiring.pdf").get_json()["file"]["id"]
    resp = client.get(f"/library/{fid}/file")
    assert resp.status_code == 200 and resp.mimetype == "application/pdf" and resp.data == PDF
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in client.get(f"/library/{fid}/file?download=1").headers["Content-Disposition"]
    client.post(f"/library/{fid}/edit", data={"name": "Wiring diagram", "description": "Cabinet A"})
    page = client.get("/library/").get_data(as_text=True)
    assert "Wiring diagram" in page and "Cabinet A" in page
    client.post(f"/library/{fid}/delete")
    assert InfoFile.query.count() == 0 and client.get(f"/library/{fid}/file").status_code == 404


def test_attach_and_detach(client, login_as, plc, db):
    """Files are attached to PLCs from the library and shown on the PLC pages."""
    login_as("admin")
    a = upload(client, PDF, "Manual.pdf").get_json()["file"]["id"]
    b = upload(client, PNG, "Photo.png").get_json()["file"]["id"]
    for fid in (a, b, a):
        resp = client.post(f"/library/api/plcs/{plc.id}/files", json={"file_id": fid})
    assert [f["name"] for f in resp.get_json()["files"]] == ["Manual", "Photo"]
    assert client.post(f"/library/api/plcs/{plc.id}/files", json={"file_id": 999}).status_code == 404
    edit = client.get(f"/admin/plcs/{plc.id}/edit").get_data(as_text=True)
    assert 'id="plc-files"' in edit and "plc_files.js" in edit and "library_upload.js" in edit
    assert "plc-file-count" in client.get("/admin/plcs").get_data(as_text=True)
    lib = client.get("/library/").get_data(as_text=True)
    assert plc.name in lib
    resp = client.delete(f"/library/api/plcs/{plc.id}/files/{a}")
    assert [f["name"] for f in resp.get_json()["files"]] == ["Photo"]
    assert db.session.get(InfoFile, a) is not None  # still in the library


def test_deleting_plc_keeps_files(client, login_as, plc, db):
    """Deleting a PLC detaches its files; the files stay in the library."""
    login_as("admin")
    fid = upload(client, PDF).get_json()["file"]["id"]
    client.post(f"/library/api/plcs/{plc.id}/files", json={"file_id": fid})
    client.post(f"/admin/plcs/{plc.id}/delete")
    item = db.session.get(InfoFile, fid)
    assert item is not None and item.plcs == []


def test_permissions(client, login_as, plc):
    """Viewers can open the library but not change it."""
    login_as("admin")
    fid = upload(client, PDF).get_json()["file"]["id"]
    client.post("/auth/logout")
    login_as("viewer")
    page = client.get("/library/").get_data(as_text=True)
    assert "library-file" not in page and client.get(f"/library/{fid}/file").status_code == 200
    assert upload(client, PNG).status_code == 403
    assert client.post(f"/library/api/plcs/{plc.id}/files", json={"file_id": fid}).status_code == 403
    assert client.post(f"/library/{fid}/delete").status_code == 403
    nav = client.get("/screens/").get_data(as_text=True).split("</nav>")[0]
    assert 'id="nav-library"' in nav and 'id="nav-documents"' in nav and 'id="nav-images"' not in nav


def test_library_menu_for_admin(client, login_as):
    """The Library menu holds Images and the information library."""
    login_as("admin")
    nav = client.get("/screens/").get_data(as_text=True).split("</nav>")[0]
    assert '<span class="hmi-nav-label">Library</span>' in nav and 'id="nav-images"' in nav
    assert 'id="nav-documents"' in nav


def test_details_lists_plc_files(client, login_as, dashboards):
    """The Details window of a screen value lists the PLC's files."""
    login_as("admin")
    fid = upload(client, PDF, "Manual.pdf").get_json()["file"]["id"]
    tag = dashboards["points"]["temp"]
    client.post(f"/library/api/plcs/{tag.plc_id}/files", json={"file_id": fid})
    d = client.get(f"/api/datapoints/{tag.id}/details").get_json()
    assert d["files"] == [{"name": "Manual", "url": f"/library/{fid}/file", "kind": "pdf"}]
