"""Tags published to other SCADA systems by the built-in Modbus TCP server."""

from sqlalchemy import false as sa_false

from app.extensions import db
from app.models.user import utcnow


class ModbusExport(db.Model):
    """One tag published on the Modbus TCP server (see :mod:`app.modbus_server`).

    The value is read only for the SCADA clients unless :attr:`writable` is
    set (the administrator's per-tag permission; the tag itself must be
    writable in pyPLCHMI too).  Registers (``area == "reg"``) can be read
    as holding registers (function 3, ``4xxxx``) **or** input registers
    (function 4, ``3xxxx``) – both show the same data; bits (``area == "bit"``)
    as coils (function 1, ``0xxxx``) or discrete inputs (function 2, ``1xxxx``).
    """

    __tablename__ = "modbus_exports"

    id = db.Column(db.Integer, primary_key=True)
    datapoint_id = db.Column(db.Integer, db.ForeignKey("datapoints.id", ondelete="CASCADE"), nullable=False,
                             unique=True)
    #: ``reg`` (registers) or ``bit`` (coils / discrete inputs).
    area = db.Column(db.String(3), nullable=False, default="reg")
    #: 0-based address of the first register / of the bit.
    address = db.Column(db.Integer, nullable=False, default=0)
    #: Modbus type (see :data:`app.modbus_server.encoding.TYPES`).
    mb_type = db.Column(db.String(8), nullable=False, default="FLOAT32")
    #: Multiplier applied to numbers before encoding (e.g. 10 → 21.5 is sent as 215).
    scale = db.Column(db.Float, nullable=False, default=1.0)
    #: Characters for ``STRING``.
    string_length = db.Column(db.Integer, nullable=False, default=0)
    #: Other SCADA systems may write this tag (function 5/15 for bits, 6/16 for registers).
    #: Effective only while the tag is writable (``DataPoint.writable``).
    writable = db.Column(db.Boolean, nullable=False, default=False, server_default=sa_false())
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    datapoint = db.relationship("DataPoint", lazy="joined",
                                backref=db.backref("modbus_export", uselist=False, cascade="all, delete-orphan"))

    def __repr__(self) -> str:
        """Debug text, e.g. ``<ModbusExport Tank_1.level reg 0 FLOAT32>``."""
        label = self.datapoint.label if self.datapoint is not None else self.datapoint_id
        return f"<ModbusExport {label} {self.area} {self.address} {self.mb_type}>"

    @property
    def count(self) -> int:
        """Registers used (1 for a bit)."""
        from app.modbus_server.encoding import register_count  # noqa: PLC0415

        return 1 if self.area == "bit" else register_count(self.mb_type, self.string_length)

    @property
    def last_address(self) -> int:
        """Last register / bit used."""
        return self.address + self.count - 1

    @property
    def write_ok(self) -> bool:
        """``True`` when SCADA clients may write the tag now (allowed here and writable in pyPLCHMI)."""
        dp = self.datapoint
        return bool(self.writable) and dp is not None and bool(dp.writable) and not dp.is_local

    def to_dict(self) -> dict:
        """JSON form for the admin page and the address list."""
        from app.modbus_server.encoding import notation  # noqa: PLC0415

        dp = self.datapoint
        return {"id": self.id, "datapoint_id": self.datapoint_id, "label": dp.label, "plc": dp.plc.name,
                "unit": dp.unit or "", "tag_type": dp.data_type, "area": self.area, "address": self.address,
                "count": self.count, "last": self.last_address, "mb_type": self.mb_type, "scale": self.scale,
                "string_length": self.string_length, "writable": bool(self.writable),
                "tag_writable": bool(dp.writable) and not dp.is_local, "write_ok": self.write_ok,
                "ref": notation(self.area, self.address), "ref_ro": notation(self.area, self.address, True)}
