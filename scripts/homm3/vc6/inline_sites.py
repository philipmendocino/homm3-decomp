"""homm3.vc6.inline_sites - map traced inline sites to source lines.

A traced site list is the candidate calls of one body in C2's tuple order:
a call is listed when it completes (after its this-expression and its
arguments), and a temporary's destructor at the end of its full
expression. The IL names a direct call's callee only through the ``addr``
tuple that opens the call expression, so a ``call`` tuple takes the most
recent unclaimed function address (a stack) and its ``end`` completes it.
Virtual and other indirect calls have no such address and can take the
wrong one; the alignment below tolerates that, since it only has to place
the traced candidates, a subsequence of the calls, at their statements.

The IL is the trace's own capture (``<trace directory>/capture``): the
same streams both C2 runs consumed, so lines refer to the traced source.
"""
from __future__ import annotations

from pathlib import Path
import re

from homm3.vc6 import il_cost

_FUNCTION = re.compile(r"__(?:thiscall|cdecl|stdcall|fastcall) (?!\*)")


def call_sites(body, names: dict, is_function) -> list[dict]:
    """The calls of one IL body in C2's site order, with the callee the
    pairing assigns and the (file, line) C1XX recorded at the call.

    A direct call is ``addr <callee>``, its this-expression, ``call``, its
    arguments and ``end``; C2 lists the site when the call completes, so
    an argument's calls precede it. A completed call followed by ``dtor``
    is a temporary's destructor, which runs at the full expression's
    ``cleanup`` (latest construction first)."""
    out, functions, frames, pending = [], [], [], []
    current_file = current_line = None
    items = [t for t in body.tuples]
    for at, item in enumerate(items):
        if item.op == il_cost.INFO_OP:
            if item.operands[0] == il_cost.INFO_LINE:
                current_line = item.operands[1]
            elif item.operands[0] == il_cost.INFO_FILE:
                current_file = names.get(item.operands[1])
            continue
        if item.op == 0x26 and item.operands and isinstance(item.operands[0], tuple):
            name = names.get(item.operands[0][1])
            if name and is_function(name):
                functions.append(name)
        elif item.op == 0x3E:
            frames.append(dict(callee=functions.pop() if functions else None,
                               file=current_file, line=current_line))
        elif item.op == 0x46:
            frames.append(None)
        elif item.op == 0x4C and frames:
            frame = frames.pop()
            if frame is None:
                continue
            following = next((t for t in items[at + 1:] if t.op != il_cost.INFO_OP), None)
            (pending if following is not None and following.op == 0x5C else out).append(frame)
        elif item.op in (0x5D, 0x5E) and pending:
            out.extend(reversed(pending))
            pending.clear()
    out.extend(reversed(pending))
    return out


def align(symbols: list[str], calls: list[dict]) -> list[int | None]:
    """For each traced symbol, the index of its call tuple (or None).

    A longest-common-subsequence alignment by callee name; traced sites are
    a subsequence of the calls (non-candidates and gated calls are not
    traced), so most align exactly."""
    n, m = len(symbols), len(calls)
    if not n or not m:
        return [None] * n
    names = [c["callee"] for c in calls]
    # Row-by-row LCS lengths; n x m stays small (a body's calls).
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, below = table[i], table[i + 1]
        for j in range(m - 1, -1, -1):
            row[j] = below[j + 1] + 1 if symbols[i] == names[j] else max(below[j], row[j + 1])
    out: list[int | None] = []
    i = j = 0
    while i < n:
        if j < m and symbols[i] == names[j] and table[i][j] == table[i + 1][j + 1] + 1:
            out.append(j)
            i += 1
            j += 1
        elif j < m and table[i][j + 1] >= table[i + 1][j]:
            j += 1
        else:
            out.append(None)
            i += 1
    return out


class SourceMap:
    """Bodies and call lines of one traced unit's captured IL."""

    def __init__(self, capture: Path, repo: Path):
        from homm3.core import undname
        from homm3.vc6 import _il
        streams = {s: (capture / f"il{s}").read_bytes() for s in _il.STREAM_ORDER
                   if (capture / f"il{s}").is_file()}
        self.repo = repo
        self.names = il_cost.gl_names(streams["gl"])
        self.bodies = {}
        for body in il_cost.functions(streams):
            if body.name and body.name not in self.bodies:
                self.bodies[body.name] = body
        mangled = [n for n in set(self.names.values()) if n.startswith("?")]
        self._demangled = undname.demangle(mangled)
        self._calls: dict[str, list[dict]] = {}
        self._texts: dict[str, list[str]] = {}

    def is_function(self, name: str) -> bool:
        if name in self.bodies:
            return True
        text = self._demangled.get(name)
        return bool(text and _FUNCTION.search(text))

    def calls(self, symbol: str) -> list[dict]:
        if symbol not in self._calls:
            body = self.bodies.get(symbol)
            self._calls[symbol] = (call_sites(body, self.names, self.is_function)
                                   if body is not None else [])
        return self._calls[symbol]

    def locate(self, owner: str, symbols: list[str]) -> list[dict | None]:
        """(file, line, text) for each site of `owner`'s traced list."""
        calls = self.calls(owner)
        out, previous = [], None
        for j in align(symbols, calls):
            if j is None:
                # Unaligned: at or after the previous aligned call.
                out.append(dict(previous, approximate=True) if previous else None)
                continue
            call = calls[j]
            previous = dict(file=self.short(call["file"]), line=call["line"],
                            text=self.text(call["file"], call["line"]))
            out.append(previous)
        return out

    def body_file(self, symbol: str) -> str | None:
        """The repository-relative file a body's first line belongs to."""
        body = self.bodies.get(symbol)
        if body is None:
            return None
        for item in body.tuples:
            if item.op == il_cost.INFO_OP and item.operands[0] == il_cost.INFO_FILE:
                return self.short(self.names.get(item.operands[1]))
        return None

    def is_library(self, symbol: str) -> bool:
        """A body outside the project's own src/ and include/ trees (the
        pinned compiler's headers, vendor code): not a source lever."""
        path = self.body_file(symbol)
        return path is not None and not path.startswith(("src/", "include/"))

    def short(self, path: str | None) -> str:
        return il_cost._short(path)

    def text(self, path: str | None, line: int | None) -> str:
        short = self.short(path)
        if short not in self._texts:
            local = self.repo / short
            if not local.is_file() and path and path[1:3] == ":\\":
                local = Path(path[2:].replace("\\", "/"))
            self._texts[short] = (local.read_text(encoding="latin1").splitlines()
                                  if local.is_file() else [])
        rows = self._texts[short]
        return rows[line - 1].strip() if line and 0 < line <= len(rows) else ""
