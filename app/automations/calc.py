"""Calculations on tags for the *Calculate* action of automations.

The user writes one assignment per line::

    tag_1 = tag_1 + 1
    [Tank level] = ([Inflow] - [Outflow]) * 0.5 + [Tank level]
    Setpoint = limit(Setpoint * 1.1, 0, 80)

* **Tags** are written by their label: a bare name for simple labels
  (letters, digits, ``_``, ``.``) or in square brackets for any label
  (``[Pump 1 speed]``).  ``[#12]`` is the tag with id 12 (for labels used by
  more than one tag).
* **Operators**: ``+ - * / %`` and parentheses; unary minus.
* **Functions**: ``abs(x)``, ``min(a, b, …)``, ``max(a, b, …)``,
  ``round(x)`` / ``round(x, digits)``, ``floor(x)``, ``ceil(x)``, ``sqrt(x)``
  and ``limit(x, low, high)``.
* Comments start with ``#`` (outside brackets); empty lines are ignored.

The text is parsed by a small parser written for this purpose – it is never
given to Python's ``eval`` – so only arithmetic is possible.

Safety (see :func:`run`):

* At save time every referenced tag must exist; sources must be numbers or
  BOOL (0/1); targets must be writable number tags.  Every problem is
  reported with its line.
* At run time all lines are calculated first (later lines see the results of
  earlier ones) and nothing is written unless every line succeeded: division
  by zero, an unreadable source tag, a result that is not a finite number, or
  a result outside the target's range (data type range in engineering units,
  and the tag's Min/Max when both are set) stop the action.  With
  ``on_range: clamp`` an out-of-range result is limited to the nearest limit
  instead (the trace says so).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.i18n.catalog import gettext

#: Functions: name -> (fewest arguments, most arguments or None).
FUNCTIONS = {"abs": (1, 1), "min": (1, None), "max": (1, None), "round": (1, 2), "floor": (1, 1),
             "ceil": (1, 1), "sqrt": (1, 1), "limit": (3, 3)}
#: Largest magnitude allowed for any intermediate result.
MAX_MAGNITUDE = 1e300
#: Longest expression text.
MAX_TEXT = 4000
#: Most assignments in one action.
MAX_LINES = 50
#: Largest float of the REAL (32 bit) data type.
REAL_MAX = 3.4028234663852886e38

_TOKEN = re.compile(r"""
    (?P<space>[ \t]+)
  | (?P<number>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<bracket>\[[^\]\n]*\])
  | (?P<name>[^\W\d][\w.]*)
  | (?P<op>[-+*/%(),=])
""", re.VERBOSE | re.UNICODE)


class CalcError(ValueError):
    """A problem in a calculation (the message is shown to the user)."""


@dataclass
class Line:
    """One parsed assignment.

    Attributes:
        number: Line number in the text (1-based).
        target: Tag reference text (label, ``#id``).
        expr: Expression tree: ``("num", v)``, ``("ref", text)``,
            ``("neg", e)``, ``("bin", op, a, b)``, ``("call", name, [args])``.
        source: The original line (for the trace).
    """

    number: int
    target: str
    expr: tuple
    source: str


# ----------------------------------------------------------------------------- parsing
def _tokens(text: str, line_no: int) -> list:
    """Split one line into tokens ``(kind, value)``."""
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise CalcError(gettext("line {0}: unexpected character '{1}'", line_no, text[pos]))
        pos = m.end()
        kind = m.lastgroup
        if kind == "space":
            continue
        value = m.group(kind)
        if kind == "bracket":
            value = value[1:-1].strip()
            if not value:
                raise CalcError(gettext("line {0}: empty tag name []", line_no))
        out.append((kind, value))
    return out


class _Parser:
    """Recursive-descent parser of one assignment line."""

    def __init__(self, tokens: list, line_no: int):
        self.t = tokens
        self.i = 0
        self.n = line_no

    def peek(self, value=None):
        """Next token (and whether it is the operator ``value``)."""
        tok = self.t[self.i] if self.i < len(self.t) else (None, None)
        return tok if value is None else (tok[0] == "op" and tok[1] == value)

    def take(self):
        """Consume the next token."""
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, value: str) -> None:
        """Consume the operator ``value`` or fail."""
        if not self.peek(value):
            found = self.peek()[1]
            raise CalcError(gettext("line {0}: '{1}' expected, found '{2}'", self.n, value,
                                    found if found is not None else gettext("end of line")))
        self.i += 1

    def assignment(self) -> tuple[str, tuple]:
        """``target = expression``."""
        kind, value = self.take()
        if kind not in ("name", "bracket"):
            raise CalcError(gettext("line {0}: must start with a tag to write, e.g. tag_1 = tag_1 + 1", self.n))
        self.expect("=")
        expr = self.expr()
        if self.i < len(self.t):
            raise CalcError(gettext("line {0}: unexpected '{1}'", self.n, self.t[self.i][1]))
        return value, expr

    def expr(self):
        """Sum: term (+|- term)*."""
        node = self.term()
        while self.peek("+") or self.peek("-"):
            op = self.take()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        """Product: unary (*|/|% unary)*."""
        node = self.unary()
        while self.peek("*") or self.peek("/") or self.peek("%"):
            op = self.take()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        """Signs."""
        if self.peek("-"):
            self.take()
            return ("neg", self.unary())
        if self.peek("+"):
            self.take()
            return self.unary()
        return self.primary()

    def primary(self):
        """Number, tag, function call or parenthesised expression."""
        kind, value = self.take()
        if kind == "number":
            return ("num", float(value) if any(c in value for c in ".eE") else int(value))
        if kind == "bracket":
            return ("ref", value)
        if kind == "name":
            if self.peek("("):
                fn = value.lower()
                if fn not in FUNCTIONS:
                    raise CalcError(gettext("line {0}: unknown function '{1}' (allowed: {2})", self.n, value,
                                            ", ".join(FUNCTIONS)))
                self.take()
                args = []
                if not self.peek(")"):
                    args.append(self.expr())
                    while self.peek(","):
                        self.take()
                        args.append(self.expr())
                self.expect(")")
                lo, hi = FUNCTIONS[fn]
                if len(args) < lo or (hi is not None and len(args) > hi):
                    raise CalcError(gettext("line {0}: wrong number of values for {1}()", self.n, fn))
                return ("call", fn, args)
            return ("ref", value)
        if kind == "op" and value == "(":
            node = self.expr()
            self.expect(")")
            return node
        raise CalcError(gettext("line {0}: a value is missing", self.n) if value is None
                        else gettext("line {0}: unexpected '{1}'", self.n, value))


def _strip_comment(line: str) -> str:
    """Remove a ``# comment`` that is not inside ``[...]``."""
    depth = 0
    for i, ch in enumerate(line):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        elif ch == "#" and depth == 0:
            return line[:i]
    return line


def parse(text: str) -> list[Line]:
    """Parse the text of a *Calculate* action.

    Raises:
        CalcError: For syntax errors (with the line number).
    """
    if len(text or "") > MAX_TEXT:
        raise CalcError(gettext("The calculation is too long (at most {0} characters).", MAX_TEXT))
    lines = []
    for n, raw in enumerate((text or "").splitlines(), start=1):
        code = _strip_comment(raw).strip()
        if not code:
            continue
        target, expr = _Parser(_tokens(code, n), n).assignment()
        lines.append(Line(n, target, expr, code))
    if not lines:
        raise CalcError(gettext("Write at least one line such as: tag_1 = tag_1 + 1"))
    if len(lines) > MAX_LINES:
        raise CalcError(gettext("Too many lines (at most {0}).", MAX_LINES))
    return lines


def refs(expr) -> list[str]:
    """Tag references used in an expression tree (in order, with repeats)."""
    if expr[0] == "ref":
        return [expr[1]]
    if expr[0] == "neg":
        return refs(expr[1])
    if expr[0] == "bin":
        return refs(expr[2]) + refs(expr[3])
    if expr[0] == "call":
        return [r for a in expr[2] for r in refs(a)]
    return []


# ----------------------------------------------------------------------------- tags
def resolve(name: str):
    """The tag referenced by ``name`` (label, or ``#id``).

    Raises:
        CalcError: If no tag (or more than one) matches.
    """
    from app.extensions import db  # noqa: PLC0415
    from app.models import DataPoint  # noqa: PLC0415

    if name.startswith("#") and name[1:].strip().isdigit():
        dp = db.session.get(DataPoint, int(name[1:]))
        if dp is None:
            raise CalcError(gettext("tag '{0}' does not exist", name))
        return dp
    found = DataPoint.query.filter(DataPoint.label == name).limit(3).all()
    if not found:
        raise CalcError(gettext("tag '{0}' does not exist", name))
    if len(found) > 1:
        ids = ", ".join(f"[#{dp.id}]" for dp in found)
        raise CalcError(gettext("tag '{0}' is ambiguous (several tags have this label) – use {1}", name, ids))
    return found[0]


def check(text: str) -> list[str]:
    """Validate a calculation for saving.

    Every line is parsed and every tag is checked: it must exist, sources must
    be numbers or BOOL, targets writable numbers (not local dummy tags).

    Returns:
        Problems, one per line/tag (empty = valid).
    """
    try:
        lines = parse(text)
    except CalcError as exc:
        return [str(exc)]
    problems: list[str] = []
    seen: set = set()

    def note(msg: str) -> None:
        if msg not in seen:
            seen.add(msg)
            problems.append(msg)

    for line in lines:
        if _zero_divisor(line.expr):
            note(gettext("line {0}: division by zero", line.number))
        try:
            dp = resolve(line.target)
            if not dp.is_numeric:
                note(gettext("line {0}: tag '{1}' cannot be calculated (data type {2}) – choose a number tag",
                             line.number, dp.label, dp.data_type))
            elif not dp.writable:
                note(gettext("line {0}: tag '{1}' is read-only – it cannot be written", line.number, dp.label))
            elif dp.is_local:
                note(gettext("line {0}: tag '{1}' is a local dummy tag – its value lives in each browser window",
                             line.number, dp.label))
        except CalcError as exc:
            note(gettext("line {0}: {1}", line.number, exc))
        for name in refs(line.expr):
            try:
                dp = resolve(name)
                if not (dp.is_numeric or dp.is_bool):
                    note(gettext("line {0}: tag '{1}' is not a number (data type {2})", line.number, dp.label,
                                 dp.data_type))
                elif dp.is_local:
                    note(gettext("line {0}: tag '{1}' is a local dummy tag – the server does not know its value",
                                 line.number, dp.label))
            except CalcError as exc:
                note(gettext("line {0}: {1}", line.number, exc))
    return problems


def _zero_divisor(expr) -> bool:
    """Whether the expression divides by a constant zero (``x / 0``, ``x % (2 - 2)``)."""
    if expr[0] == "bin":
        if expr[1] in "/%" and not refs(expr[3]):
            try:
                if evaluate(expr[3], {}, 0) == 0:
                    return True
            except CalcError:
                pass
        return _zero_divisor(expr[2]) or _zero_divisor(expr[3])
    if expr[0] == "neg":
        return _zero_divisor(expr[1])
    if expr[0] == "call":
        return any(_zero_divisor(a) for a in expr[2])
    return False


def tag_limits(dp) -> tuple[float, float]:
    """Lowest and highest value a tag can take, in engineering units.

    The data type's raw range is converted with the tag's scale and offset;
    when the tag has both Min and Max set, they narrow the range.
    """
    from app.plc.datatypes import DATA_TYPES  # noqa: PLC0415

    info = DATA_TYPES.get(dp.data_type)
    if info is not None and info.min is not None:
        raw_lo, raw_hi = info.min, info.max
    elif dp.data_type == "REAL":
        raw_lo, raw_hi = -REAL_MAX, REAL_MAX
    else:
        raw_lo, raw_hi = -1.7976931348623157e308, 1.7976931348623157e308
    scale = dp.scale if dp.scale not in (None, 0) else 1.0
    offset = dp.offset or 0.0
    a, b = raw_lo * scale + offset, raw_hi * scale + offset
    lo, hi = min(a, b), max(a, b)
    if dp.min_value is not None and dp.max_value is not None and dp.min_value < dp.max_value:
        lo, hi = max(lo, dp.min_value), min(hi, dp.max_value)
    return lo, hi


# ----------------------------------------------------------------------------- evaluation
def _num(value, line_no: int) -> float:
    """Guard: a finite number of reasonable size."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, float) and math.isinf(value):
        raise CalcError(gettext("line {0}: the number is too large (overflow)", line_no))
    if not isinstance(value, (int, float)) or isinstance(value, float) and math.isnan(value):
        raise CalcError(gettext("line {0}: the result is not a valid number", line_no))
    if abs(value) > MAX_MAGNITUDE:
        raise CalcError(gettext("line {0}: the number is too large (overflow)", line_no))
    return value


def evaluate(expr, values: dict, line_no: int):
    """Calculate an expression tree.

    Args:
        expr: Tree from :func:`parse`.
        values: Tag reference text -> current number.
        line_no: For messages.

    Raises:
        CalcError: Division by zero, invalid results, overflow.
    """
    kind = expr[0]
    if kind == "num":
        return expr[1]
    if kind == "ref":
        return _num(values[expr[1]], line_no)
    if kind == "neg":
        return _num(-evaluate(expr[1], values, line_no), line_no)
    if kind == "bin":
        op, a, b = expr[1], evaluate(expr[2], values, line_no), evaluate(expr[3], values, line_no)
        if op in "/%" and b == 0:
            raise CalcError(gettext("line {0}: division by zero", line_no))
        try:
            result = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a / b,
                      "%": lambda: math.fmod(a, b)}[op]()
        except OverflowError:
            raise CalcError(gettext("line {0}: the number is too large (overflow)", line_no)) from None
        if op == "%" and isinstance(a, int) and isinstance(b, int):
            result = int(result)
        return _num(result, line_no)
    if kind == "call":
        fn, args = expr[1], [evaluate(a, values, line_no) for a in expr[2]]
        if fn == "sqrt" and args[0] < 0:
            raise CalcError(gettext("line {0}: square root of a negative number", line_no))
        if fn == "limit" and args[1] > args[2]:
            raise CalcError(gettext("line {0}: limit(): the low value is above the high value", line_no))
        result = {
            "abs": lambda: abs(args[0]), "min": lambda: min(args), "max": lambda: max(args),
            "round": lambda: round(args[0], int(args[1])) if len(args) > 1 else int(round(args[0])),
            "floor": lambda: math.floor(args[0]), "ceil": lambda: math.ceil(args[0]),
            "sqrt": lambda: math.sqrt(args[0]), "limit": lambda: min(max(args[0], args[1]), args[2]),
        }[fn]()
        return _num(result, line_no)
    raise CalcError(f"unknown node {kind}")


def plan(text: str, read, clamp: bool = False) -> list[dict]:
    """Calculate every line without writing anything.

    Args:
        text: The calculation.
        read: Function ``(DataPoint) -> value dict`` (``{"value", "quality"}``).
        clamp: Limit out-of-range results instead of failing.

    Returns:
        ``[{"tag", "line", "value", "before", "note"}]`` – one write per line, in order.

    Raises:
        CalcError: On any problem (nothing may be written then).
    """
    lines = parse(text)
    problems = check(text)
    if problems:
        raise CalcError("; ".join(problems))
    values: dict = {}     # reference text -> number (updated by earlier lines)

    def current(name: str, line_no: int):
        """Value of a tag: calculated by an earlier line, else read from the PLC."""
        dp = resolve(name)
        key = ("id", dp.id)
        if key not in values:
            reading = read(dp)
            if not reading or reading.get("quality") != "good" or reading.get("value") is None:
                raise CalcError(gettext("line {0}: tag '{1}' cannot be read", line_no, dp.label))
            value = reading["value"]
            values[key] = int(value) if isinstance(value, bool) else value
        return values[key]

    out = []
    for line in lines:
        env = {name: current(name, line.number) for name in refs(line.expr)}
        result = evaluate(line.expr, env, line.number)
        target = resolve(line.target)
        try:
            before = current(line.target, line.number)
        except CalcError:
            before = None      # only used for the audit log
        from app.plc.datatypes import DATA_TYPES  # noqa: PLC0415

        note = ""
        if DATA_TYPES[target.data_type].integer and (target.scale in (None, 1.0) and not target.offset):
            if result != int(round(result)):
                note = gettext("rounded from {0}", _fmt(result))
            result = int(round(result))
        lo, hi = tag_limits(target)
        if result < lo or result > hi:
            if not clamp:
                raise CalcError(gettext("line {0}: {1} is outside the range of '{2}' ({3} … {4})", line.number,
                                        _fmt(result), target.label, _fmt(lo), _fmt(hi)))
            limited = lo if result < lo else hi
            note = gettext("limited from {0} to {1}", _fmt(result), _fmt(limited))
            result = int(limited) if isinstance(result, int) and float(limited).is_integer() else limited
        values[("id", target.id)] = result
        out.append({"tag": target, "line": line, "value": result, "before": before, "note": note})
    return out


def _fmt(number) -> str:
    """Short text of a number (``16.83``, ``-32768``, ``1.5e+20``)."""
    if isinstance(number, float):
        if number.is_integer() and abs(number) < 1e15:
            return str(int(number))
        return f"{number:.6g}"
    return str(number)
