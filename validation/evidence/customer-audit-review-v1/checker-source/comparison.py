"""Contract-driven table comparison. No table inference, numeric coercion, or rounding-sort."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import math


@dataclass(frozen=True)
class Column:
    name: str
    kind: str = "exact"
    abs_tol: Decimal = Decimal(0)
    rel_tol: Decimal = Decimal(0)
    null_equal: bool = True
    nan_equal: bool = False

    def __post_init__(self):
        if self.kind not in {"exact", "integer", "boolean", "text", "date", "datetime", "numeric"}:
            raise ValueError(f"Unknown column kind: {self.kind}")
        for name in ("abs_tol", "rel_tol"):
            value = Decimal(str(getattr(self, name)))
            if not value.is_finite() or value < 0:
                raise ValueError("Tolerances must be finite and nonnegative")
            object.__setattr__(self, name, value)
        if self.kind != "numeric" and (self.abs_tol or self.rel_tol):
            raise ValueError("Only declared numeric measures accept tolerance")


@dataclass(frozen=True)
class TableContract:
    columns: tuple[Column, ...]
    keys: tuple[str, ...] = ()
    ordered: bool = False
    allow_extra_columns: bool = False
    max_unkeyed_rows: int = 2000

    def __post_init__(self):
        names = [c.name for c in self.columns]
        if not names or len(names) != len(set(names)):
            raise ValueError("Declare unique output columns explicitly")
        if not set(self.keys) <= set(names) or len(self.keys) != len(set(self.keys)):
            raise ValueError("Unknown or duplicate key columns")
        if any(c.name in self.keys and (c.abs_tol or c.rel_tol) for c in self.columns):
            raise ValueError("Keys must be exact")


@dataclass(frozen=True)
class Comparison:
    equal: bool
    reason: str


def _valid(value, kind):
    if value is None:
        return True
    return {"integer": lambda: type(value) is int,
            "boolean": lambda: type(value) is bool,
            "text": lambda: type(value) is str,
            "date": lambda: type(value) is date,
            "datetime": lambda: type(value) is datetime,
            "numeric": lambda: type(value) in (int, float, Decimal),
            "exact": lambda: True}[kind]()


def _same(a, b, column):
    if not _valid(a, column.kind) or not _valid(b, column.kind):
        return False
    if a is None or b is None:
        return a is None and b is None and column.null_equal
    if column.kind != "numeric":
        return type(a) is type(b) and a == b
    a, b = Decimal(str(a)), Decimal(str(b))
    if a.is_nan() or b.is_nan():
        return a.is_nan() and b.is_nan() and column.nan_equal
    if not a.is_finite() or not b.is_finite():
        return a == b
    return abs(a - b) <= max(column.abs_tol, column.rel_tol * max(abs(a), abs(b)))


def compare_tables(gold, prediction, contract):
    """Rows are mappings. Extra columns, keys, types, NULL/NaN and order are explicit."""
    gold, prediction = list(gold), list(prediction)
    names = {c.name for c in contract.columns}
    if len(gold) != len(prediction):
        return Comparison(False, f"Row count {len(prediction)} != {len(gold)}")
    for label, rows in (("gold", gold), ("prediction", prediction)):
        for row in rows:
            if not names <= row.keys():
                return Comparison(False, f"Missing columns in {label}")
            if not contract.allow_extra_columns and set(row) != names:
                return Comparison(False, f"Extra columns in {label}")
            if any(not _valid(row[c.name], c.kind) for c in contract.columns):
                return Comparison(False, f"Column type mismatch in {label}")
    def same(g, p):
        return all(_same(g[c.name], p[c.name], c) for c in contract.columns)
    def key(row):
        return tuple((type(row[k]).__name__, row[k]) for k in contract.keys)
    if contract.keys:
        indexes = []
        for label, rows in (("gold", gold), ("prediction", prediction)):
            index = {}
            for row in rows:
                k = key(row)
                if any(row[x] is None or isinstance(row[x], (float, Decimal)) and math.isnan(row[x]) for x in contract.keys):
                    return Comparison(False, f"NULL/NaN unique key in {label}")
                if k in index:
                    return Comparison(False, f"Duplicate key in {label}: {k}")
                index[k] = row
            indexes.append(index)
        gmap, pmap = indexes
        if gmap.keys() != pmap.keys():
            return Comparison(False, "Key set mismatch")
        if contract.ordered and [key(r) for r in gold] != [key(r) for r in prediction]:
            return Comparison(False, "Row order mismatch")
        ok = all(same(g, pmap[k]) for k, g in gmap.items())
        return Comparison(ok, "Key-aligned values match" if ok else "Key-aligned values differ")
    if contract.ordered:
        ok = all(same(g, p) for g, p in zip(gold, prediction))
        return Comparison(ok, "Ordered rows match" if ok else "Ordered rows differ")
    if len(gold) > contract.max_unkeyed_rows:
        raise ValueError("Unkeyed tolerance matching limit exceeded; specify keys or an explicit task comparator")
    # A perfect bipartite matching retains multiplicity and handles non-transitive tolerances.
    edges = [[j for j, p in enumerate(prediction) if same(g, p)] for g in gold]
    match_g, match_p = {}, {}
    for start in range(len(gold)):
        queue, parents, visited = deque([start]), {}, {start}
        end = None
        while queue and end is None:
            g = queue.popleft()
            for p in edges[g]:
                if p in parents:
                    continue
                parents[p] = g
                if p not in match_p:
                    end = p
                    break
                next_g = match_p[p]
                if next_g not in visited:
                    visited.add(next_g)
                    queue.append(next_g)
        if end is None:
            return Comparison(False, "No multiplicity-preserving row matching")
        while end is not None:
            g = parents[end]
            previous = match_g.get(g)
            match_g[g], match_p[end] = end, g
            end = previous
    return Comparison(True, "Multiset rows match")
