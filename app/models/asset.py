"""Uploaded image assets used by graphic (Image) screens, and the library folders."""

from app.extensions import db
from app.models.user import utcnow


class Asset(db.Model):
    """An image file (PNG, JPEG, GIF, WebP or SVG) uploaded by an admin.

    The file itself lives in ``UPLOAD_FOLDER`` under a random name
    (:attr:`filename`); only metadata is stored in the database.  Assets are
    shared by all dashboards and are referenced from layouts by :attr:`id`.
    """

    __tablename__ = "assets"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    filename = db.Column(db.String(64), unique=True, nullable=False)
    mimetype = db.Column(db.String(40), nullable=False)
    size = db.Column(db.Integer, nullable=False, default=0)
    #: Library folder path, e.g. ``"Images"`` or ``"Animations/HVAC"`` (see :class:`AssetFolder`).
    category = db.Column(db.String(200), nullable=False, default="Images", index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    uploaded_by = db.relationship("User")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<Asset 3 fan.gif>``."""
        return f"<Asset {self.id} {self.name}>"

    def to_dict(self) -> dict:
        """Serialise for the designer's image palette.

        Returns:
            Dict with ``id``, ``name``, ``category`` (= folder path),
            ``folder`` (same value), ``mimetype``, ``size`` and ``url``.
        """
        from flask import url_for  # noqa: PLC0415

        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "folder": self.category,
            "mimetype": self.mimetype,
            "size": self.size,
            "url": url_for("assets.file", asset_id=self.id),
        }


class AssetFolder(db.Model):
    """A folder of the image library.

    Folders are identified by their full path with ``/`` separators
    (``"Animations/HVAC"``); every parent path has its own row, so empty
    folders can exist.  Images refer to their folder by path
    (:attr:`Asset.category`).
    """

    __tablename__ = "asset_folders"

    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(200), unique=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<AssetFolder Animations/HVAC>``."""
        return f"<AssetFolder {self.path}>"

    @property
    def name(self) -> str:
        """Last path segment (the folder's own name)."""
        return self.path.rsplit("/", 1)[-1]

    @property
    def parent_path(self) -> str:
        """Path of the parent folder, ``""`` for a top-level folder."""
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""
