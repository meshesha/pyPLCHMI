"""Protocol registry: maps a PLC's ``protocol`` to its address parser.

Protocols:

* ``s7``         – Siemens S7 via python-snap7 (addresses like ``DB1.DBD4``)
* ``modbus_tcp`` – Modbus TCP via pymodbus **or** pyModbusTCP (``HR100``, ``40001``)
* ``modbus_rtu`` – Modbus RTU over a serial port via pymodbus
* ``bacnet_ip``  – BACnet/IP via BAC0 (``AV3``, ``BO12``, ``AI1.units``)
* ``dummy``      – no device: internal values, tags need no address (see :mod:`app.plc.dummy`)
"""

from app.plc.addressing import AddressError, S7Address, parse_address
from app.plc.bacnet import BACnetAddress, BACnetAddressError, parse_bacnet_address
from app.plc.dummy import DummyAddress, DummyAddressError, parse_dummy_address
from app.plc.modbus import ModbusAddress, ModbusAddressError, parse_modbus_address

#: ``protocol -> label`` for select fields.
PROTOCOLS = {
    "s7": "Siemens S7 (snap7)",
    "modbus_tcp": "Modbus TCP",
    "modbus_rtu": "Modbus RTU (serial)",
    "bacnet_ip": "BACnet/IP (BAC0)",
    "dummy": "Dummy (no device – internal values)",
}

#: Client libraries available for Modbus TCP.
MODBUS_LIBRARIES = {"pymodbus": "PyModbus", "pymodbustcp": "pyModbusTCP"}

#: Default TCP port per protocol.
DEFAULT_PORTS = {"s7": 102, "modbus_tcp": 502, "modbus_rtu": 0, "bacnet_ip": 47808, "dummy": 0}

#: Address examples shown in forms.
ADDRESS_HELP = {
    "s7": "e.g. DB1.DBD4, DB1.DBX0.0, M10.5, MW20, IW64, Q0.1, T5",
    "modbus": "e.g. HR100, HR100:REAL, HR10.3 (bit), IR5, C12 (coil), DI3, 40001, 30010",
    "bacnet": "e.g. AV3, AI1, BO12, MV2, AI1.units, AV5@10 (write priority)",
}


def is_modbus(protocol: str | None) -> bool:
    """Tell whether a protocol code is a Modbus variant.

    Args:
        protocol: Protocol code.

    Returns:
        ``True`` for ``modbus_tcp`` and ``modbus_rtu``.
    """
    return bool(protocol) and protocol.startswith("modbus")


def parse_tag_address(text: str, data_type=None, string_length=None, protocol: str = "s7"):
    """Parse an address with the syntax of the given protocol.

    Args:
        text: Address text.
        data_type: Default data type when the text has no ``:TYPE`` suffix.
        string_length: STRING length when not given as ``[n]``.
        protocol: PLC protocol code.

    Returns:
        :class:`~app.plc.addressing.S7Address` or :class:`~app.plc.modbus.ModbusAddress`.

    Raises:
        AddressError: If the address is invalid (Modbus errors are re-raised
            as :class:`AddressError` so callers handle one exception type).
    """
    if is_modbus(protocol):
        try:
            return parse_modbus_address(text, data_type, string_length)
        except ModbusAddressError as exc:
            raise AddressError(str(exc)) from exc
    if protocol == "bacnet_ip":
        try:
            return parse_bacnet_address(text, data_type, string_length)
        except BACnetAddressError as exc:
            raise AddressError(str(exc)) from exc
    if protocol == "dummy":
        try:
            return parse_dummy_address(text, data_type, string_length)
        except DummyAddressError as exc:
            raise AddressError(str(exc)) from exc
    return parse_address(text, data_type, string_length)


def make_address(protocol: str, area: str, db_number: int, start: int, bit: int, data_type: str, string_length: int,
                 property_name: str = "", priority: int = 0):
    """Rebuild an address object from stored data point fields.

    Args:
        protocol: PLC protocol code.
        area: Area / object code.
        db_number: DB number (S7 only).
        start: Start address / object instance.
        bit: Bit number.
        data_type: Type name.
        string_length: STRING length.
        property_name: BACnet property (empty = presentValue).
        priority: BACnet write priority (0 = device default).

    Returns:
        :class:`S7Address`, :class:`ModbusAddress` or :class:`BACnetAddress`.
    """
    if is_modbus(protocol):
        return ModbusAddress(area=area, start=start, bit=bit, data_type=data_type, string_length=string_length)
    if protocol == "bacnet_ip":
        return BACnetAddress(area=area, start=start, property_name=property_name or "presentValue",
                             priority=priority or 0, data_type=data_type)
    if protocol == "dummy":
        return DummyAddress(start=start, data_type=data_type, string_length=string_length)
    return S7Address(area=area, db_number=db_number, start=start, bit=bit, data_type=data_type, string_length=string_length)


def syntax_family(protocol: str | None) -> str:
    """Group a protocol code into the address syntax it uses.

    Args:
        protocol: Protocol code.

    Returns:
        ``"s7"``, ``"modbus"``, ``"bacnet"`` or ``"dummy"``.
    """
    if is_modbus(protocol):
        return "modbus"
    if protocol == "dummy":
        return "dummy"
    return "bacnet" if protocol == "bacnet_ip" else "s7"
