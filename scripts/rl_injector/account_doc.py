"""Byte-faithful model of a decrypted RemoteLink account document.

RemoteLink's import format is deliberately narrow: one ``Panels/Panel``
document, no inter-tag whitespace, and leaf fields shaped exactly as
``<NAME DataType="N">value</NAME>``.  The parser accepts only that verified
shape.  Rejecting unfamiliar input is safer than silently normalizing an
account that will later be sent to a panel.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Iterator

from .errors import InjectorError


_ROOT_OPEN = "<Panels><Panel>"
_ROOT_CLOSE = "</Panel></Panels>"
_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_OPEN_RE = re.compile(rf"<({_NAME})>")
_LEAF_RE = re.compile(
    rf'<({_NAME}) DataType="(\d+)">([^<]*)</\1>'
)


def _b64(text: str) -> str:
    """Encode a DataType=1 value using RemoteLink's padding convention."""
    raw = text.encode("latin-1")
    raw += b"\x00" * ((3 - len(raw) % 3) % 3)
    return base64.b64encode(raw).decode("ascii")


def _unb64(value: str) -> str:
    """Decode a RemoteLink DataType=1 value and remove its NUL fill."""
    return base64.b64decode(value).decode("latin-1").rstrip("\x00")


@dataclass
class Field:
    name: str
    data_type: str
    raw: str

    @property
    def text(self) -> str:
        return _unb64(self.raw) if self.data_type == "1" else self.raw

    def set_text(self, value: str) -> None:
        value = str(value)
        self.raw = _b64(value) if self.data_type == "1" else value

    def serialize(self) -> str:
        return (
            f'<{self.name} DataType="{self.data_type}">'
            f"{self.raw}</{self.name}>"
        )


@dataclass
class Row:
    name: str
    fields: list[Field]

    def find(self, tag: str) -> Field | None:
        return next((field for field in self.fields if field.name == tag), None)

    def require(self, tag: str) -> Field:
        field = self.find(tag)
        if field is None:
            raise InjectorError(f"<{self.name}> has no <{tag}> field")
        return field

    def text(self, tag: str, default: str | None = None) -> str | None:
        field = self.find(tag)
        return field.text if field is not None else default

    def set(self, tag: str, value: str) -> bool:
        field = self.find(tag)
        if field is None:
            return False
        field.set_text(value)
        return True

    def clone(self) -> "Row":
        return Row(
            self.name,
            [Field(field.name, field.data_type, field.raw) for field in self.fields],
        )

    def serialize(self) -> str:
        return (
            f"<{self.name}>"
            + "".join(field.serialize() for field in self.fields)
            + f"</{self.name}>"
        )


@dataclass
class Table:
    name: str
    rows: list[Row]

    def serialize(self) -> str:
        return (
            f"<{self.name}>"
            + "".join(row.serialize() for row in self.rows)
            + f"</{self.name}>"
        )


@dataclass
class AccountDoc:
    nodes: list[Row | Table]

    def find(self, name: str) -> Row | Table | None:
        return next((node for node in self.nodes if node.name == name), None)

    def row(self, name: str) -> Row:
        node = self.find(name)
        if not isinstance(node, Row):
            kind = "missing" if node is None else "a table"
            raise InjectorError(f"<{name}> is {kind}, not a field block")
        return node

    def table(self, name: str) -> Table:
        node = self.find(name)
        if not isinstance(node, Table):
            kind = "missing" if node is None else "a field block"
            raise InjectorError(f"<{name}> is {kind}, not a table")
        return node

    def find_row(self, name: str) -> Row | None:
        node = self.find(name)
        return node if isinstance(node, Row) else None

    def find_table(self, name: str) -> Table | None:
        node = self.find(name)
        return node if isinstance(node, Table) else None

    def iter_rows(self) -> Iterator[Row]:
        for node in self.nodes:
            if isinstance(node, Row):
                yield node
            else:
                yield from node.rows

    def serialize(self) -> str:
        return (
            _ROOT_OPEN
            + "".join(node.serialize() for node in self.nodes)
            + _ROOT_CLOSE
        )


def _error(text: str, pos: int, message: str) -> InjectorError:
    snippet = text[pos:pos + 48]
    return InjectorError(f"{message} at offset {pos}: {snippet!r}")


def _leaf_at(text: str, pos: int) -> tuple[Field, int] | None:
    match = _LEAF_RE.match(text, pos)
    if match is None:
        return None
    return Field(match.group(1), match.group(2), match.group(3)), match.end()


def _parse_row_body(text: str, pos: int, row_name: str) -> tuple[list[Field], int]:
    fields: list[Field] = []
    close = f"</{row_name}>"
    while not text.startswith(close, pos):
        parsed = _leaf_at(text, pos)
        if parsed is not None:
            field, pos = parsed
            fields.append(field)
            continue
        if _OPEN_RE.match(text, pos):
            raise _error(text, pos, f"unsupported nesting inside row <{row_name}>")
        raise _error(text, pos, f"expected a field or {close} for <{row_name}>")
    if not fields:
        raise _error(text, pos, f"row <{row_name}> has no fields")
    return fields, pos + len(close)


def parse_account_xml(text: str) -> AccountDoc:
    """Parse the exact RemoteLink account XML shape without normalizing it."""
    if not text.startswith(_ROOT_OPEN):
        raise InjectorError(
            "not a RemoteLink account document; must start with <Panels><Panel>"
        )

    pos = len(_ROOT_OPEN)
    panel_close = "</Panel>"
    nodes: list[Row | Table] = []

    while not text.startswith(panel_close, pos):
        outer = _OPEN_RE.match(text, pos)
        if outer is None:
            raise _error(text, pos, "expected a Panel child")
        name = outer.group(1)
        pos = outer.end()
        outer_close = f"</{name}>"

        if text.startswith(outer_close, pos):
            nodes.append(Table(name, []))
            pos += len(outer_close)
            continue

        first_leaf = _leaf_at(text, pos)
        if first_leaf is not None:
            fields, pos = _parse_row_body(text, pos, name)
            nodes.append(Row(name, fields))
            continue

        if _OPEN_RE.match(text, pos) is None:
            raise _error(text, pos, f"cannot classify <{name}>")

        rows: list[Row] = []
        while not text.startswith(outer_close, pos):
            row_open = _OPEN_RE.match(text, pos)
            if row_open is None:
                raise _error(text, pos, f"expected a row or {outer_close}")
            row_name = row_open.group(1)
            fields, pos = _parse_row_body(text, row_open.end(), row_name)
            rows.append(Row(row_name, fields))
        pos += len(outer_close)
        nodes.append(Table(name, rows))

    pos += len(panel_close)
    panels_close = "</Panels>"
    if not text.startswith(panels_close, pos):
        raise _error(text, pos, f"expected {panels_close}")
    pos += len(panels_close)
    if pos != len(text):
        raise _error(text, pos, "trailing content after </Panels>")
    return AccountDoc(nodes)
