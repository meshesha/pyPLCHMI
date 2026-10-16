"""Information library: documents (images and PDFs) attached to PLCs / devices.

* :class:`InfoFile` – one stored file (manual, wiring diagram, data sheet,
  photo …).  The SHA-1 hash of the content is stored in :attr:`InfoFile.sha1`
  (unique), so the same file is never stored twice.
* ``plc_files`` – which files are attached to which PLC (any number in both
  directions).

Files are kept in ``<UPLOAD_FOLDER>/library/`` named after their hash; the
original file name is kept for downloads.
"""

from __future__ import annotations

from app.extensions import db
from app.models.user import utcnow

#: Accepted file types: extension -> (MIME type, kind).
FILE_TYPES = {
    "pdf": ("application/pdf", "pdf"),
    "png": ("image/png", "image"),
    "jpg": ("image/jpeg", "image"),
    "jpeg": ("image/jpeg", "image"),
    "gif": ("image/gif", "image"),
    "webp": ("image/webp", "image"),
    "bmp": ("image/bmp", "image"),
}

plc_files = db.Table(
    "plc_files",
    db.Column("plc_id", db.Integer, db.ForeignKey("plcs.id", ondelete="CASCADE"), primary_key=True),
    db.Column("file_id", db.Integer, db.ForeignKey("info_files.id", ondelete="CASCADE"), primary_key=True),
    db.Column("added_at", db.DateTime(timezone=True), nullable=True, default=utcnow),
)


class InfoFile(db.Model):
    """A document of the information library."""

    __tablename__ = "info_files"

    id = db.Column(db.Integer, primary_key=True)
    #: Display name (editable).
    name = db.Column(db.String(200), nullable=False)
    #: File name as uploaded (used for downloads).
    original_name = db.Column(db.String(255), nullable=False, default="")
    #: Stored file name in the library folder (``<sha1>.<ext>``).
    filename = db.Column(db.String(64), unique=True, nullable=False)
    mimetype = db.Column(db.String(60), nullable=False)
    size = db.Column(db.Integer, nullable=False, default=0)
    #: SHA-1 of the content (40 hex digits) – duplicates are refused.
    sha1 = db.Column(db.String(40), unique=True, nullable=False, index=True)
    description = db.Column(db.String(500), nullable=False, default="", server_default="")
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    uploaded_by = db.relationship("User")
    plcs = db.relationship("PLC", secondary=plc_files, back_populates="files", order_by="PLC.name")

    def __repr__(self) -> str:
        """Return a debug representation, e.g. ``<InfoFile manual.pdf>``."""
        return f"<InfoFile {self.name}>"

    def __str__(self) -> str:
        """The display name (used in the audit log)."""
        return self.name

    @property
    def extension(self) -> str:
        """Lower-case extension of the stored file (``pdf``, ``png`` …)."""
        return self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""

    @property
    def kind(self) -> str:
        """``pdf`` or ``image``."""
        return FILE_TYPES.get(self.extension, ("", "pdf"))[1]

    @property
    def is_image(self) -> bool:
        """``True`` for pictures (shown as thumbnails)."""
        return self.kind == "image"

    @property
    def size_text(self) -> str:
        """Human-readable size, e.g. ``1.4 MB``."""
        size = float(self.size or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    def to_dict(self) -> dict:
        """JSON representation for the library and PLC pages.

        Returns:
            Dict with id, names, type, size, hash, URLs and attached PLC names.
        """
        from flask import url_for  # noqa: PLC0415

        from app.models.alarm import iso  # noqa: PLC0415

        return {
            "id": self.id, "name": self.name, "original_name": self.original_name, "kind": self.kind,
            "mimetype": self.mimetype, "size": self.size, "size_text": self.size_text, "sha1": self.sha1,
            "description": self.description, "created_at": iso(self.created_at),
            "uploaded_by": self.uploaded_by.username if self.uploaded_by else None,
            "url": url_for("documents.file", file_id=self.id),
            "download": url_for("documents.file", file_id=self.id, download=1),
            "plcs": [p.name for p in self.plcs],
        }
