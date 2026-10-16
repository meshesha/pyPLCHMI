"""Tests for the image library folders and the built-in animations."""

import json
import os

import pytest
from PIL import Image

from app.assets.folders import (
    BUILTIN_DIR,
    FolderError,
    ancestors,
    builtin_animations,
    delete_folder,
    ensure_folder,
    folder_tree,
    import_builtin_animations,
    normalize_path,
    rename_folder,
    store_asset,
)
from app.extensions import db
from app.models import Asset, AssetFolder
from tests.test_assets import GIF, png_bytes, upload

JSON = {"Accept": "application/json"}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Animations/HVAC", "Animations/HVAC"), (" /a // b/ ", "a/b"), ("a\\b", "a/b"), ("", "Images"),
    (None, "Images"), ("Équipement/Pompes", "Équipement/Pompes"),
])
def test_normalize_path(raw, expected):
    """Paths are trimmed, separators collapsed, empty paths use the default."""
    assert normalize_path(raw) == expected


@pytest.mark.parametrize("raw", ["a/../b", "x/.", "bad:name", "a/b*", "x" * 61, "/".join(["abcdefghij"] * 20)])
def test_normalize_path_rejects(raw):
    """Dot segments, forbidden characters and over-long names/paths are refused."""
    with pytest.raises(FolderError):
        normalize_path(raw)


def test_normalize_required():
    """With ``default=None`` an empty path is an error."""
    with pytest.raises(FolderError):
        normalize_path("  / ", default=None)


def test_ancestors():
    """Every parent path is listed, top-level first."""
    assert ancestors("a/b/c") == ["a", "a/b", "a/b/c"]


# ---------------------------------------------------------------------------
# Folder operations
# ---------------------------------------------------------------------------
def test_ensure_folder_creates_parents(app):
    """Creating a nested folder also creates its parents, only once."""
    ensure_folder("Plant/Boilers/B1")
    ensure_folder("Plant/Boilers")
    db.session.commit()
    assert sorted(f.path for f in AssetFolder.query) == ["Plant", "Plant/Boilers", "Plant/Boilers/B1"]
    assert AssetFolder.query.filter_by(path="Plant/Boilers").one().name == "Boilers"
    assert AssetFolder.query.filter_by(path="Plant/Boilers").one().parent_path == "Plant"


def test_tree_counts(app):
    """The tree counts images directly inside and including sub-folders."""
    store_asset(png_bytes(2, 2), "a", "Plant/Pumps")
    store_asset(png_bytes(3, 2), "b", "Plant")
    ensure_folder("Empty")
    db.session.commit()
    tree = {n["path"]: n for n in folder_tree()}
    assert tree["Plant"]["count"] == 1 and tree["Plant"]["total"] == 2
    assert tree["Plant/Pumps"]["depth"] == 1 and tree["Plant/Pumps"]["parent"] == "Plant"
    assert tree["Empty"]["total"] == 0


def test_tree_includes_folders_without_rows(app):
    """Images whose folder row is missing still show their folder."""
    asset, _ = store_asset(png_bytes(), "x", "A")
    asset.category = "Legacy/Cat"
    db.session.commit()
    assert {"Legacy", "Legacy/Cat"} <= {n["path"] for n in folder_tree()}


def test_rename_folder_moves_subfolders_and_images(app):
    """Renaming a folder updates its sub-folders and the images in them."""
    a, _ = store_asset(png_bytes(2, 2), "a", "Old/Sub")
    b, _ = store_asset(png_bytes(3, 3), "b", "Old")
    c, _ = store_asset(png_bytes(4, 4), "c", "Older")
    db.session.commit()
    assert rename_folder("Old", "Group/New") == "Group/New"
    db.session.commit()
    assert (a.category, b.category, c.category) == ("Group/New/Sub", "Group/New", "Older")
    assert sorted(f.path for f in AssetFolder.query) == ["Group", "Group/New", "Group/New/Sub", "Older"]


def test_rename_folder_merges(app):
    """Renaming onto an existing folder merges both."""
    store_asset(png_bytes(2, 2), "a", "A/X")
    store_asset(png_bytes(3, 3), "b", "B/X")
    db.session.commit()
    rename_folder("A", "B")
    db.session.commit()
    assert sorted(f.path for f in AssetFolder.query) == ["B", "B/X"]
    assert Asset.query.filter_by(category="B/X").count() == 2


def test_rename_folder_errors(app):
    """Unknown folders and moves into the folder itself are refused."""
    ensure_folder("A/B")
    db.session.commit()
    with pytest.raises(FolderError):
        rename_folder("Nope", "X")
    with pytest.raises(FolderError):
        rename_folder("A", "A/B/C")


def test_delete_folder(app):
    """Only folders without images (also in sub-folders) can be deleted."""
    ensure_folder("Empty/Sub")
    store_asset(png_bytes(), "a", "Full/Sub")
    db.session.commit()
    with pytest.raises(FolderError):
        delete_folder("Full")
    assert delete_folder("Empty") == 2
    db.session.commit()
    assert not AssetFolder.query.filter(AssetFolder.path.like("Empty%")).count()


def test_store_asset_reuses_identical(app):
    """Identical content is stored once unless ``reuse=False``."""
    first, created = store_asset(GIF, "dot", "Icons")
    again, created2 = store_asset(GIF, "dot copy", "Other")
    assert created and not created2 and again.id == first.id
    _third, created3 = store_asset(GIF, "dot 2", "Icons", reuse=False)
    assert created3 and Asset.query.count() == 2


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def test_upload_into_folder(client, login_as):
    """Uploads go to the ``folder`` field (created on the fly); ``category`` still works."""
    login_as("admin")
    a = upload(client, png_bytes(), "p.png", folder="Plant/Pumps").get_json()["assets"][0]
    b = upload(client, png_bytes(5, 5), "q.png", category="Legacy").get_json()["assets"][0]
    assert a["folder"] == "Plant/Pumps" and b["category"] == "Legacy"
    assert {"Plant", "Plant/Pumps", "Legacy"} <= {f.path for f in AssetFolder.query}
    resp = upload(client, png_bytes(), "r.png", folder="bad|name")
    assert resp.status_code == 400 and "Invalid folder" in resp.get_json()["errors"][0]


def test_library_views(client, login_as):
    """Folder page, breadcrumb, sub-folder tiles and search."""
    login_as("admin")
    upload(client, png_bytes(), "Main pump.png", folder="Plant/Pumps")
    html = client.get("/assets/?folder=Plant").data.decode()
    assert 'data-drop-folder="Plant/Pumps"' in html and "Main pump" not in html
    html = client.get("/assets/?folder=Plant/Pumps").data.decode()
    assert "Main pump" in html and "breadcrumb" in html and 'value="Plant/Pumps"' in html
    html = client.get("/assets/?q=main").data.decode()
    assert "Main pump" in html and "Plant/Pumps" in html
    assert client.get("/assets/?folder=Nope").status_code == 302


def test_folder_create_rename_delete_views(client, login_as):
    """The folder forms create, rename/move and delete folders."""
    login_as("admin")
    resp = client.post("/assets/folders/create", data={"parent": "Plant", "name": "Boilers/Room 1"})
    assert resp.status_code == 302 and "folder=Plant/Boilers/Room" in resp.headers["Location"].replace("%2F", "/")
    assert AssetFolder.query.filter_by(path="Plant/Boilers/Room 1").count() == 1
    client.post("/assets/folders/rename", data={"path": "Plant/Boilers", "new_path": "Heating"})
    assert {"Heating", "Heating/Room 1"} <= {f.path for f in AssetFolder.query}
    client.post("/assets/folders/delete", data={"path": "Heating"})
    assert not AssetFolder.query.filter(AssetFolder.path.like("Heating%")).count()
    client.post("/assets/folders/create", data={"parent": "", "name": "a:b"})
    assert not AssetFolder.query.filter_by(path="a:b").count()


def test_delete_folder_view_refuses_non_empty(client, login_as):
    """A folder that holds images stays."""
    login_as("admin")
    upload(client, png_bytes(), "p.png", folder="Keep")
    resp = client.post("/assets/folders/delete", data={"path": "Keep"}, follow_redirects=True)
    assert b"not empty" in resp.data and AssetFolder.query.filter_by(path="Keep").count() == 1


def test_move_images(client, login_as):
    """Selected images move to the target folder (form and AJAX)."""
    login_as("admin")
    ids = [upload(client, png_bytes(i + 2, 2), f"{i}.png").get_json()["assets"][0]["id"] for i in range(3)]
    client.post("/assets/move", data={"asset_ids": ids[:2], "folder": "Moved/Here", "back": "Images"})
    assert [db.session.get(Asset, i).category for i in ids] == ["Moved/Here", "Moved/Here", "Images"]
    resp = client.post("/assets/move", data={"asset_ids": [ids[2]], "folder": "Moved"}, headers=JSON)
    assert resp.get_json() == {"ok": True, "moved": 1, "folder": "Moved"}
    assert client.post("/assets/move", data={"asset_ids": [ids[2]], "folder": ""}, headers=JSON).status_code == 400


def test_rename_image_folder(client, login_as):
    """The rename form can also change the folder."""
    login_as("admin")
    aid = upload(client, png_bytes(), "p.png").get_json()["assets"][0]["id"]
    client.post(f"/assets/{aid}/rename", data={"name": "Pump", "folder": "Equipment/Pumps"})
    asset = db.session.get(Asset, aid)
    assert asset.name == "Pump" and asset.category == "Equipment/Pumps"


def test_folder_views_need_permission(client, login_as):
    """Viewers cannot change folders."""
    login_as("viewer")
    for url in ("/assets/folders/create", "/assets/folders/rename", "/assets/folders/delete", "/assets/move",
                "/assets/animations"):
        assert client.post(url, data={"name": "x", "path": "x", "folder": "x"}).status_code in (302, 403)
    assert not AssetFolder.query.count()


# ---------------------------------------------------------------------------
# Built-in animations
# ---------------------------------------------------------------------------
def test_builtin_manifest_files_are_animated_gifs():
    """Every manifest entry exists, is an animated GIF with transparency and in a known category."""
    items = builtin_animations()
    assert len(items) >= 30
    cats = {i["category"] for i in items}
    assert {"HVAC", "Electrical", "Water", "Generators", "Fuel"} <= cats
    for item in items:
        path = os.path.join(BUILTIN_DIR, item["file"])
        assert item["file"].startswith(item["category"] + "/")
        with Image.open(path) as im:
            assert im.format == "GIF" and 2 <= im.n_frames <= item["frames"]  # identical frames are merged
            assert "transparency" in im.info
        assert os.path.getsize(path) < 1024 * 1024


def test_builtin_manifest_matches_generator():
    """The manifest lists exactly the animations of ``tools/make_animations.py``."""
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        "make_animations", os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "make_animations.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with open(os.path.join(BUILTIN_DIR, "manifest.json"), encoding="utf-8") as fh:
        files = [a["file"] for a in json.load(fh)["animations"]]
    assert files == [f"{c}/{stem}.gif" for c, stem, *_ in mod.ANIMATIONS]


def test_generator_renders_frames(tmp_path):
    """The generator can rebuild one animation."""
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        "make_animations", os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "make_animations.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build(str(tmp_path), size=64, only="axial-fan")
    with Image.open(tmp_path / "HVAC" / "axial-fan.gif") as im:
        assert im.size == (64, 64) and im.n_frames == 12


def test_import_builtin_animations_idempotent(app):
    """Animations land in Animations/<Category>; a second run adds nothing."""
    first = import_builtin_animations()
    db.session.commit()
    total = len(builtin_animations())
    assert first["added"] == total and first["skipped"] == 0
    assert "Animations/HVAC" in first["folders"]
    assert Asset.query.filter_by(category="Animations/Water").count() >= 5
    pump = Asset.query.filter_by(name="Centrifugal pump").one()
    assert pump.mimetype == "image/gif" and pump.category == "Animations/Water"
    pump.category = "My pumps"  # moved by the user: not imported again
    db.session.commit()
    second = import_builtin_animations()
    assert second["added"] == 0 and second["skipped"] == total


def test_add_animations_button(client, login_as):
    """The library button imports the animations and opens the Animations folder."""
    login_as("admin")
    assert b"Add built-in animations" in client.get("/assets/").data
    resp = client.post("/assets/animations")
    assert resp.status_code == 302 and "folder=Animations" in resp.headers["Location"]
    html = client.get("/assets/?folder=Animations").data.decode()
    for cat in ("HVAC", "Electrical", "Water", "Generators", "Fuel"):
        assert f'data-drop-folder="Animations/{cat}"' in html
    resp = client.post("/assets/animations", follow_redirects=True)
    assert b"already in the library" in resp.data


def test_cli_library(app):
    """``flask library animations`` and ``flask library folders``."""
    runner = app.test_cli_runner()
    out = runner.invoke(args=["library", "animations"]).output
    assert "animation(s) added" in out and "Animations/Fuel" in out
    out = runner.invoke(args=["library", "folders"]).output
    assert "Animations" in out and "  HVAC" in out


def test_designer_receives_folders(client, login_as, app):
    """The designer data carries each image's folder."""
    login_as("admin")
    upload(client, png_bytes(), "p.png", folder="Plant/Pumps")
    assert client.get("/assets/list").get_json()[0]["folder"] == "Plant/Pumps"
