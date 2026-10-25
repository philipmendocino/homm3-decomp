"""homm3.vc6.inline_solve - where and what kind of source difference an
inline wall needs.

`predict-inline --tu UNIT` traces every budget test C2 makes in a unit and
the budget replay (`inline_replay`) reproduces them. Retail's object then
shows which of a root's traced callees retail keeps as calls. When no root
cb reproduces those counts (`--cb-window` says "none"), retail's site set
or callee costs differ from ours. This module searches small hypothesis
spaces over the traced site tree for the fewest, smallest edits whose replay
gives retail's retained-call count for every traced callee:

- ``cb``      the root's own cost (its budget is clamp(2 x cb, 1000, 35000));
- ``insert``  K free (cb <= 40) candidate sites after a site of one site
              list: the root's own list is a caller edit, a callee's list
              an edit of that helper's body (every expansion of it);
- ``remove``  one site (a helper call our source has that retail may not);
- ``callee``  a callee's cost changed by d at every site; under /O1 a cost
              reaching 128 makes it a non-candidate (inliner.md section 3).

Each fit carries the root-cb window over which it still holds, because
real source edits move the caller's own cost too (an accessor call costs a
few tuples). Sites map to source through the trace's own captured IL: the
call tuples of each body, in order, with the line C1XX recorded.

Nothing here is source to adopt. A hypothesis names the place and the kind
of difference; the matcher looks for native evidence there (Mac retained
calls, Loki or Dreamcast structure) before changing C++. The project's
rules forbid budget-steering code (AGENTS.md, helper boundaries).

Known limits: children of a site that no traced expansion of the same
callee reveals are unknown (marked incomplete); a site that newly expands
takes its veto verdict from that callee's other traced sites; sites C2
rejects before the budget test (argument count, caller EH state) are not
in the trace, so their calls never change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import re
import time

from homm3.vc6.inline_replay import SIZE_CAP, traced_expansion, tree

#: C2's candidacy limit DAT_10799280 is 16 << 3 = 128 unless option bit23
#: is set (the game's /O2 profile), when it is 1000.
CANDIDATE_LIMIT_O1 = 128
CANDIDATE_LIMIT_O2 = 1000
FREE = 40


def candidate_limit(flags: list[str]) -> int:
    """The collector's cb limit for a unit's flag profile."""
    return CANDIDATE_LIMIT_O1 if any(f.lstrip("/-") == "O1" for f in flags) \
        else CANDIDATE_LIMIT_O2


_expanded = traced_expansion


def templates(roots: list[dict]) -> dict[str, dict]:
    """Callee -> its largest traced expansion in the unit (the node, with
    its children and veto record).

    A callee's candidate sites come from its one stored body, so an
    expansion traced in any root shows the sites a refused occurrence
    would have had."""
    best: dict[str, tuple[int, list]] = {}

    def size(nodes):
        return sum(1 + size(n["children"]) for n in nodes)

    def walk(nodes):
        for node in nodes:
            if _expanded(node):
                n = size(node["children"])
                if n > best.get(node["symbol"], (-1, None))[0]:
                    best[node["symbol"]] = (n, node)
            walk(node["children"])
    for root in roots:
        walk(tree([dict(s, index=i) for i, s in enumerate(root["sites"])]))
    return {symbol: node for symbol, (_n, node) in best.items()}


def _kept(site: dict) -> bool:
    return _expanded(site) and not (site.get("veto") or {}).get("vetoed", False)


def _veto_total(veto: dict) -> int:
    """The substituted body's whole weighted count (a vetoed walk stops at
    the first count above the limit; the shim finishes it as `total`)."""
    return veto.get("total", veto["count"])


class VetoTable:
    """Per callee, the veto totals traced for each pattern of its kept
    children, so a what-if that changes a nested decision re-judges the
    veto. Across every veto record of the editor traces the verdict is
    exactly total > limit; the total depends on which children were kept
    (vector<TRmgMapPosition>::push_back totals 7 with its insert refused and
    15 with it kept, against a limit of 12). A pattern never traced is
    estimated from a traced one plus per-child increments measured on
    pattern pairs differing in that child alone."""

    def __init__(self, roots: list[dict]):
        self.estimates = 0
        self.totals: dict[str, dict[tuple, list[int]]] = {}
        for root in roots:
            def walk(nodes):
                for node in nodes:
                    veto = node.get("veto")
                    if veto and _expanded(node):
                        shape = tuple(c["symbol"] for c in node["children"])
                        pattern = tuple(_kept(c) for c in node["children"])
                        self.totals.setdefault(node["symbol"], {}).setdefault(
                            (shape, pattern), []).append(_veto_total(veto))
                    walk(node["children"])
            walk(tree([dict(s, index=i) for i, s in enumerate(root["sites"])]))
        self.increments: dict[str, dict[tuple, int]] = {}
        for symbol, seen in self.totals.items():
            steps: dict[tuple, list[int]] = {}
            for (shape, a), ta in seen.items():
                for (other, b), tb in seen.items():
                    if other != shape or a == b:
                        continue
                    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
                    if len(diff) == 1 and a[diff[0]]:
                        steps.setdefault((shape, diff[0]), []).append(
                            _mode(ta) - _mode(tb))
            self.increments[symbol] = {k: sorted(v)[len(v) // 2] for k, v in steps.items()}

    def total(self, symbol: str, shape: tuple, pattern: tuple) -> int | None:
        """The traced total for this pattern, else an estimate (counted in
        `estimates`), else None."""
        seen = self.totals.get(symbol, {})
        if (shape, pattern) in seen:
            return _mode(seen[(shape, pattern)])
        self.estimates += 1
        steps = self.increments.get(symbol, {})
        best = None
        for (other, known), totals in seen.items():
            if other != shape:
                continue
            diff = [i for i, (x, y) in enumerate(zip(known, pattern)) if x != y]
            if all((shape, i) in steps for i in diff) and (best is None or len(diff) < best[0]):
                value = _mode(totals) + sum(steps[(shape, i)] * (1 if pattern[i] else -1)
                                            for i in diff)
                best = (len(diff), value)
        return best[1] if best else None


def _mode(values: list[int]) -> int:
    return max(set(values), key=values.count)


def veto_votes(roots: list[dict]) -> dict[str, bool]:
    """Callee -> whether most of its traced veto records revert the copy."""
    votes: dict[str, list[int]] = {}
    for root in roots:
        for site in root["sites"]:
            veto = site.get("veto")
            if veto:
                tally = votes.setdefault(site["symbol"], [0, 0])
                tally[bool(veto["vetoed"])] += 1
    return {symbol: yes > no for symbol, (no, yes) in votes.items()}


@dataclass
class Model:
    """A root's site tree, flattened, with template children attached."""
    root: dict
    limit: int = CANDIDATE_LIMIT_O2
    sym: list = field(default_factory=list)
    cost: list = field(default_factory=list)
    remaining: list = field(default_factory=list)
    vetoed: list = field(default_factory=list)     # traced (or voted) verdict
    veto_limit: list = field(default_factory=list)  # None: no veto record
    veto_pattern: list = field(default_factory=list)  # traced kept-children pattern
    gate: list = field(default_factory=list)        # argument count matches
    index: list = field(default_factory=list)       # trace index, -1 synthetic
    anchor: list = field(default_factory=list)      # nearest traced ancestor
    known: list = field(default_factory=list)       # children are known
    kids: list = field(default_factory=list)
    top: list = field(default_factory=list)
    lists: list = field(default_factory=list)       # (owner symbol, nodes)
    siblings: list = field(default_factory=list)    # node -> (its list, position)
    parent: list = field(default_factory=list)

    @classmethod
    def build(cls, root: dict, *, unit_roots: list[dict] | None = None,
              limit: int = CANDIDATE_LIMIT_O2, template_depth: int = 6) -> "Model":
        model = cls(root, limit)
        shapes = templates(unit_roots or [root])
        votes = veto_votes(unit_roots or [root])
        model.vetoes = VetoTable(unit_roots or [root])

        def add(node, anchor, path, synthetic):
            n = len(model.sym)
            model.sym.append(node["symbol"])
            model.cost.append(node["cb"])
            model.remaining.append(node["remaining"])
            veto = node.get("veto")
            model.vetoed.append(bool(veto["vetoed"]) if veto
                                else votes.get(node["symbol"], False))
            model.gate.append(node.get("args_match", True)
                              and not node.get("context_rejected", False))
            index = -1 if synthetic else node["index"]
            model.index.append(index)
            model.anchor.append(index if index >= 0 else anchor)
            model.kids.append([])
            children, known = node["children"], (not synthetic and _expanded(node))
            if synthetic:
                known = True
            record = node if veto else None
            if not known and node["symbol"] in shapes and node["symbol"] not in path \
                    and len(path) < template_depth:
                template = shapes[node["symbol"]]
                children, known, synthetic = template["children"], True, True
                record = template if template.get("veto") else None
            model.veto_limit.append(record["veto"]["limit"] if record else None)
            model.veto_pattern.append(tuple(_kept(c) for c in record["children"])
                                      if record else None)
            model.known.append(known)
            kids = [add(c, model.anchor[n], path | {node["symbol"]}, synthetic)
                    for c in children]
            model.kids[n] = kids
            if kids:
                model.lists.append((node["symbol"], kids))
            return n
        model.top = [add(node, -1, frozenset(), False)
                     for node in tree([dict(s, index=i) for i, s in enumerate(root["sites"])])]
        model.lists.insert(0, (None, model.top))
        model.siblings = [None] * len(model.sym)
        model.parent = [-1] * len(model.sym)
        for _owner, nodes in model.lists:
            for position, n in enumerate(nodes):
                model.siblings[n] = (nodes, position)
        for n, kids in enumerate(model.kids):
            for kid in kids:
                model.parent[kid] = n
        return model

    def top_ancestor(self, n: int) -> int:
        while self.parent[n] >= 0:
            n = self.parent[n]
        return n

    # -- replay ----------------------------------------------------------

    def run(self, cb: int | None = None, cost: dict | None = None,
            extra: dict | None = None, removed: frozenset | set = frozenset(),
            calls: frozenset | set = frozenset()):
        """Decisions under an edit: per node expanded or not, the per-callee
        retained-call counts of the reached sites, and whether a newly
        expanded site's children are unknown. `removed` sites are not in
        the source; `calls` are no longer candidates (they stay calls)."""
        cb = self.root["cb"] if cb is None else cb
        costs = self.cost if not cost else [cost.get(n, c) for n, c in enumerate(self.cost)]
        rem = self.remaining if not extra else [r + extra.get(n, 0)
                                                for n, r in enumerate(self.remaining)]
        limit, kids, gate = self.limit, self.kids, self.gate
        vetoed = self.now_vetoed = list(self.vetoed)
        self.vetoes.estimates = 0
        dropped = set(removed) | set(calls)
        expanded = [False] * len(costs)
        budgets = self.budgets = [None] * len(costs)
        size = [cb]

        def walk(nodes, budget):
            left = budget
            for n in nodes:
                if n in dropped:
                    continue
                c = costs[n]
                budgets[n] = left
                if not ((c <= FREE or left >= c) and size[0] < SIZE_CAP and gate[n]):
                    continue
                expanded[n] = True
                charge = c if c > FREE else 0
                nested = (left - charge) // max(1, rem[n])
                left -= charge
                size[0] += c
                spent = walk(kids[n], nested)
                if self.veto_limit[n] is not None:
                    vetoed[n] = self._judge(n, expanded, vetoed, dropped)
                if not vetoed[n]:
                    left -= spent
                    size[0] += spent
            return budget - left
        walk(self.top, min(35000, max(1000, 2 * cb)))
        counts: dict[str, int] = {}
        incomplete = False
        reached = self.reached = [False] * len(costs)
        stack = list(self.top)
        while stack:
            n = stack.pop()
            if n in removed:
                continue
            reached[n] = True
            if expanded[n] and not vetoed[n]:
                if not self.known[n]:
                    incomplete = True
                stack.extend(kids[n])
            else:
                counts[self.sym[n]] = counts.get(self.sym[n], 0) + 1
        return expanded, counts, incomplete

    def _judge(self, n: int, expanded: list, vetoed: list, dropped: set) -> bool:
        """The veto verdict for n's substituted body under the children
        decisions just made."""
        kids = self.kids[n]
        pattern = tuple(expanded[c] and not vetoed[c] and c not in dropped for c in kids)
        if pattern == self.veto_pattern[n]:
            return self.vetoed[n]
        total = self.vetoes.total(self.sym[n], tuple(self.sym[c] for c in kids), pattern)
        if total is None:
            return self.vetoed[n]
        return total > self.veto_limit[n]

    @property
    def estimated(self) -> int:
        """Veto verdicts the last run estimated from an untraced pattern."""
        return self.vetoes.estimates

    def reproduces(self) -> bool:
        """The replay's decisions equal every traced site's."""
        expanded, _counts, _incomplete = self.run()
        sites = self.root["sites"]
        return all(expanded[n] == _expanded(sites[i])
                   and (not sites[i].get("veto") or self.now_vetoed[n] == sites[i]["veto"]["vetoed"])
                   for n, i in enumerate(self.index) if i >= 0)


# -- edits ----------------------------------------------------------------

@dataclass(frozen=True)
class Edit:
    kind: str            # cb | insert | remove | callee
    owner: str | None = None   # insert/remove: list owner (None = the root)
    position: int = 0          # insert/remove: position in that list
    symbol: str | None = None  # callee
    amount: int = 0            # cb/callee delta, insert count

    def magnitude(self) -> float:
        if self.kind == "insert":
            return float(self.amount)
        if self.kind == "remove":
            return 1.0
        return abs(self.amount) / 8.0


def _apply(model: Model, edits) -> dict:
    """Edits -> replay arguments (cb, cost, extra, removed)."""
    cb = model.root["cb"]
    cost: dict[int, int] = {}
    extra: dict[int, int] = {}
    removed: set[int] = set()
    calls: set[int] = set()
    for edit in edits:
        if edit.kind == "cb":
            cb += edit.amount
        elif edit.kind == "callee":
            for n, symbol in enumerate(model.sym):
                if symbol == edit.symbol:
                    cost[n] = max(1, model.cost[n] + edit.amount)
                    if cost[n] >= model.limit:
                        # No longer collected: one fewer remaining site
                        # for every earlier sibling.
                        calls.add(n)
                        nodes, position = model.siblings[n]
                        for earlier in nodes[:position]:
                            extra[earlier] = extra.get(earlier, 0) - 1
        else:
            for owner, nodes in model.lists:
                if owner != edit.owner or edit.position >= len(nodes):
                    continue
                if edit.kind == "insert":
                    for n in nodes[:edit.position + 1]:
                        extra[n] = extra.get(n, 0) + edit.amount
                else:
                    removed.add(nodes[edit.position])
                    for n in nodes[:edit.position]:
                        extra[n] = extra.get(n, 0) - 1
    return dict(cb=cb, cost=cost, extra=extra, removed=removed, calls=calls)


def _shapes(model: Model) -> list[tuple[str | None, list[int]]]:
    """One representative list per owner whose expansions agree in shape."""
    seen: dict = {}
    for owner, nodes in model.lists:
        shape = tuple(model.sym[n] for n in nodes)
        if owner in seen:
            if seen[owner] is not None and seen[owner][0] != shape:
                seen[owner] = None
            continue
        seen[owner] = (shape, nodes)
    return [(owner, value[1]) for owner, value in seen.items() if value is not None]


@dataclass
class Fit:
    edits: tuple
    distance: int
    incomplete: bool
    flips: list
    window: tuple | None = None
    span: tuple | None = None       # insert: first and last fitting position
    trivial: bool = False           # removes a site of a callee it must change
    library: bool = False           # edits a library (system header) body
    estimated: bool = False         # a veto verdict came from an untraced pattern
    deltas_by_callee: dict | None = None   # callee -> modelled call change
    counts: tuple | None = None     # insert: fewest and most fitting sites
    deltas: tuple | None = None     # callee: fitting cost deltas

    def key(self):
        holds = (self.window is not None and self.window[0] <= 0 <= self.window[1]) \
            or all(e.kind == "cb" for e in self.edits)
        return (self.distance, len(self.edits), self.trivial, self.library,
                self.incomplete or self.estimated, not holds,
                sum(e.magnitude() for e in self.edits))


class Search:
    """The bounded hypothesis search for one root."""

    def __init__(self, model: Model, wanted: dict[str, int], *, max_insert: int = 12,
                 callee_range: int = 48, seeds: int = 40, seconds: float = 60.0,
                 nested: bool = True, library: frozenset | set = frozenset()):
        self.model, self.wanted = model, wanted
        self.max_insert, self.callee_range = max_insert, callee_range
        self.seeds, self.deadline = seeds, time.monotonic() + seconds
        self.nested = nested
        self.library = set(library)        # callees whose bodies are fixed
        self.base_expanded, self.base_counts, _ = model.run()
        self.base_reached = list(model.reached)
        self.base_vetoed = list(model.now_vetoed)
        self.shapes = dict(_shapes(model))
        self.evaluated = 0
        self.timed_out = False

    def distance(self, counts: dict) -> int:
        names = set(counts) | set(self.base_counts) | set(self.wanted)
        return sum(abs(counts.get(s, 0) - self.base_counts.get(s, 0) - self.wanted.get(s, 0))
                   for s in names)

    def evaluate(self, edits) -> Fit:
        self.evaluated += 1
        args = _apply(self.model, edits)
        expanded, counts, incomplete = self.model.run(**args)
        reached, budgets = self.model.reached, self.model.budgets
        vetoed, before = self.model.now_vetoed, self.base_vetoed
        flips = [(n, budgets[n] if n not in args["calls"] else "non-candidate")
                 for n, (a, b) in enumerate(zip(expanded, self.base_expanded))
                 if (a and not vetoed[n]) != (b and not before[n])
                 and (reached[n] or self.base_reached[n]) and n not in args["removed"]]
        trivial = any(e.kind == "remove" and self._removed_symbol(e) in self.wanted
                      for e in edits)
        library = any((e.kind == "callee" and e.symbol in self.library)
                      or (e.kind in ("insert", "remove") and e.owner in self.library)
                      for e in edits)
        changed = {k: counts.get(k, 0) - self.base_counts.get(k, 0)
                   for k in set(counts) | set(self.base_counts)
                   if counts.get(k, 0) != self.base_counts.get(k, 0)}
        return Fit(tuple(edits), self.distance(counts), incomplete, flips,
                   trivial=trivial, library=library, deltas_by_callee=changed,
                   estimated=self.model.estimated > 0)

    def _removed_symbol(self, edit: Edit) -> str | None:
        nodes = self.shapes.get(edit.owner, [])
        return self.model.sym[nodes[edit.position]] if edit.position < len(nodes) else None

    def window(self, fit: Fit, span: int) -> tuple | None:
        """Root-cb deltas around zero (within span) for which `fit` holds."""
        others = [e for e in fit.edits if e.kind != "cb"]
        shift = sum(e.amount for e in fit.edits if e.kind == "cb")

        def holds(delta):
            return self.evaluate(others + [Edit("cb", amount=shift + delta)]).distance == 0
        if not holds(0):
            return None
        low = high = 0
        while low > -span and holds(low - 1):
            low -= 1
        while high < span and holds(high + 1):
            high += 1
        return (low + shift, high + shift)

    def partial(self, scored: list[Fit], top: int) -> dict:
        """Per wanted callee, the smallest edits that give exactly its change
        and move no other callee away from retail: independent pieces of a
        wall that no single small edit set closes."""
        out = {}
        for symbol, delta in self.wanted.items():
            pieces = []
            for fit in scored:
                got = fit.deltas_by_callee or {}
                if got.get(symbol, 0) != delta:
                    continue
                if any(abs(got.get(k, 0) - self.wanted.get(k, 0)) > abs(self.wanted.get(k, 0))
                       for k in set(got) if k != symbol):
                    continue
                if any(k not in self.wanted for k in got):
                    continue
                pieces.append(fit)
            pieces = self.collapse(pieces[:top * 20])
            pieces.sort(key=lambda f: f.key()[1:])
            out[symbol] = pieces[:top]
        return out

    def collapse(self, fits: list[Fit]) -> list[Fit]:
        """Merge fits that differ only along one dimension of one edit: an
        insertion's position in its list (it may go anywhere in the run),
        then its count, and a callee's cost delta."""
        def runs(items, dimension, merge):
            groups: dict = {}
            out = []
            for fit in items:
                varying = [e for e in fit.edits if dimension(e) is not None]
                if len(varying) != 1:
                    out.append(fit)
                    continue
                edit = varying[0]
                rest = tuple(sorted((e for e in fit.edits if e is not edit), key=repr))
                key = (edit.kind, edit.owner, edit.symbol, rest, merge.key(edit, fit))
                groups.setdefault(key, []).append((dimension(edit), fit))
            for members in groups.values():
                members.sort(key=lambda m: m[0])
                run: list = []
                for value, fit in members:
                    if run and value != run[-1][0] + 1:
                        out.append(merge.done(run))
                        run = []
                    run.append((value, fit))
                out.append(merge.done(run))
            return out

        class Position:
            key = staticmethod(lambda edit, fit: edit.amount)

            @staticmethod
            def done(run):
                fit = run[0][1]
                fit.span = (run[0][0], run[-1][0])
                return fit

        class Count:
            key = staticmethod(lambda edit, fit: (edit.position, fit.span))

            @staticmethod
            def done(run):
                fit = run[0][1]
                fit.counts = (run[0][0], run[-1][0])
                return fit

        class Delta:
            key = staticmethod(lambda edit, fit: edit.amount > 0)

            @staticmethod
            def done(run):
                fit = min((f for _v, f in run), key=lambda f: sum(
                    abs(e.amount) for e in f.edits if e.kind == "callee"))
                fit.deltas = (run[0][0], run[-1][0])
                return fit
        fits = runs(fits, lambda e: e.position if e.kind == "insert" else None, Position)
        fits = runs(fits, lambda e: e.amount if e.kind == "insert" else None, Count)
        return runs(fits, lambda e: e.amount if e.kind == "callee" else None, Delta)

    def singles(self) -> list[Edit]:
        model, out = self.model, []
        for owner, nodes in _shapes(model):
            if owner is not None and not self.nested:
                continue
            for position in range(len(nodes)):
                out.append(Edit("remove", owner, position))
                out.extend(Edit("insert", owner, position, amount=k)
                           for k in range(1, self.max_insert + 1))
        for symbol in sorted(set(model.sym)):
            low = min(c for c, s in zip(model.cost, model.sym) if s == symbol)
            out.extend(Edit("callee", symbol=symbol, amount=d)
                       for d in range(-min(low - 1, self.callee_range), self.callee_range + 1)
                       if d)
        return out

    def cb_fits(self) -> list[Fit]:
        """Root-cb-only windows (the existing --cb-window answer)."""
        cb = self.model.root["cb"]
        fits, run = [], None
        for value in range(max(1, cb // 2), min(17600, max(cb * 2 + 200, cb + 1000))):
            fit = self.evaluate([Edit("cb", amount=value - cb)])
            if fit.distance == 0:
                if run and run[1] == value - 1:
                    run[1] = value
                else:
                    run = [value, value, fit]
                    fits.append(run)
        out = []
        for low, high, fit in fits:
            nearest = min(range(low, high + 1), key=lambda v: abs(v - cb))
            fit = Fit((Edit("cb", amount=nearest - cb),), 0, fit.incomplete, fit.flips,
                      window=(low - cb, high - cb))
            out.append(fit)
        return out

    def solve(self, *, max_edits: int = 2, top: int = 8, span: int = 60) -> dict:
        base = self.distance(self.base_counts)
        result = dict(base_distance=base, fits=[], best=[], evaluated=0)
        if base == 0:
            return result
        fits = self.cb_fits()
        scored = []
        for edit in self.singles():
            if time.monotonic() > self.deadline:
                self.timed_out = True
                break
            fit = self.evaluate([edit])
            if fit.distance == 0:
                fits.append(fit)
            elif fit.distance < base:
                scored.append(fit)
        scored.sort(key=Fit.key)
        if max_edits >= 2 and not self.timed_out:
            pool = [f.edits[0] for f in scored[:self.seeds]]
            pool += [f.edits[0] for f in fits if f.edits[0].kind != "cb"][:self.seeds]
            for a, b in itertools.combinations(dict.fromkeys(pool), 2):
                if time.monotonic() > self.deadline:
                    self.timed_out = True
                    break
                if (a.kind, a.owner, a.position, a.symbol) == (b.kind, b.owner, b.position, b.symbol) \
                        or (a.kind == b.kind == "cb"):
                    continue
                if a.kind == b.kind == "callee" and a.symbol == b.symbol:
                    continue
                fit = self.evaluate([a, b])
                if fit.distance == 0:
                    fits.append(fit)
                else:
                    scored.append(fit)
            # A seed plus the root's own mass: an edit that adds or drops
            # sites moves the caller's cost too.
            cb = self.model.root["cb"]
            reach = min(cb // 4, 300)
            scored.sort(key=Fit.key)
            singles = [f for f in scored if len(f.edits) == 1][:self.seeds]
            for seed in singles:
                for delta in sorted(range(-reach, reach + 1), key=abs):
                    if not delta:
                        continue
                    if time.monotonic() > self.deadline:
                        self.timed_out = True
                        break
                    fit = self.evaluate([seed.edits[0], Edit("cb", amount=delta)])
                    if fit.distance == 0:
                        fits.append(fit)
                        break
        unique: dict = {}
        for fit in fits:
            unique.setdefault(frozenset(fit.edits), fit)
        fits = list(unique.values())
        for fit in fits:
            if fit.window is None:
                fit.window = self.window(fit, span)
        fits = self.collapse(fits)
        # Drop fits that only add a further edit to a smaller fit.
        fits.sort(key=Fit.key)
        kept: list[Fit] = []
        for fit in fits:
            if any(set(k.edits) < set(fit.edits) for k in kept):
                continue
            kept.append(fit)
        scored.sort(key=Fit.key)
        best = self.collapse(scored[:top * 20])
        best.sort(key=Fit.key)
        result.update(fits=kept[:top], best=best[:top] if not kept else [],
                      partial=self.partial(scored, top) if not kept else {},
                      evaluated=self.evaluated, timed_out=self.timed_out,
                      total_fits=len(kept))
        return result


def what_if(model: Model, *, cb: int | None = None, adds=(), removes=(), costs=()) -> dict:
    """One manual what-if over trace indices: `adds` (index, count) inserts
    free sites after that site in its own list only, `removes` drops sites,
    `costs` (needle, cb) sets every matching callee's cost."""
    by_index = {i: n for n, i in enumerate(model.index) if i >= 0}
    extra: dict[int, int] = {}
    removed: set[int] = set()
    calls: set[int] = set()
    cost: dict[int, int] = {}

    def shift(n, amount, inclusive):
        nodes, position = model.siblings[n]
        for earlier in nodes[:position + (1 if inclusive else 0)]:
            extra[earlier] = extra.get(earlier, 0) + amount
    for index, count in adds:
        if index not in by_index:
            raise ValueError(f"no trace site {index}")
        shift(by_index[index], count, True)
    for index in removes:
        if index not in by_index:
            raise ValueError(f"no trace site {index}")
        removed.add(by_index[index])
        shift(by_index[index], -1, False)
    for needle, value in costs:
        for n, symbol in enumerate(model.sym):
            if needle in symbol:
                cost[n] = value
                if value >= model.limit:
                    calls.add(n)
                    shift(n, -1, False)
    base_expanded, base_counts, _ = model.run()
    base_budgets, base_reached = list(model.budgets), list(model.reached)
    base_vetoed = list(model.now_vetoed)
    expanded, counts, incomplete = model.run(cb=cb, cost=cost, extra=extra,
                                             removed=removed, calls=calls)
    flips = []
    for n, (a, b) in enumerate(zip(expanded, base_expanded)):
        if (a == b and model.now_vetoed[n] == base_vetoed[n]) or n in removed \
                or not (model.reached[n] or base_reached[n]):
            continue
        flips.append(dict(index=model.index[n], anchor=model.anchor[n],
                          depth=_depth(model, n), symbol=model.sym[n],
                          cb=cost.get(n, model.cost[n]), before=base_budgets[n],
                          after=model.budgets[n],
                          verdict=("expands, then vetoed" if model.now_vetoed[n] else "expands")
                          if a else "refused"))
    changed = {k: counts.get(k, 0) - base_counts.get(k, 0)
               for k in set(counts) | set(base_counts)
               if counts.get(k, 0) != base_counts.get(k, 0)}
    return dict(flips=flips, calls=changed, incomplete=incomplete)


def _depth(model: Model, n: int) -> int:
    depth = 1
    while model.parent[n] >= 0:
        n = model.parent[n]
        depth += 1
    return depth


def wanted_deltas(row: dict) -> dict[str, int]:
    """`tu_divergences` row -> callee -> retail calls minus ours."""
    return {c["callee"]: c["retail_calls"] - c["base_calls"] for c in row["callees"]}


# -- report ---------------------------------------------------------------

def locations(model: Model, smap) -> dict[int, dict]:
    """Node -> source location of its call, through the trace's IL."""
    out: dict[int, dict] = {}
    if smap is None:
        return out
    for owner, nodes in model.lists:
        symbol = owner or model.root["symbol"]
        for n, loc in zip(nodes, smap.locate(symbol, [model.sym[n] for n in nodes])):
            if loc:
                out[n] = loc
    return out


_STRING = "std::basic_string<char, struct std::char_traits<char>, class std::allocator<char>>"


_CONVENTION = re.compile(r"^.*?__(?:thiscall|cdecl|stdcall|fastcall) ")


def _pretty(name: str) -> str:
    name = _CONVENTION.sub("", name)
    return name.replace(_STRING, "std::string").replace("struct ", "").replace("class ", "")


class Renderer:
    def __init__(self, model: Model, smap=None):
        self.model, self.smap = model, smap
        self.locs = locations(model, smap)
        self.shapes = dict(_shapes(model))

    def name(self, symbol: str | None) -> str:
        if symbol is None:
            return model_root_label(self.model)
        if self.smap is not None:
            from homm3.core import undname
            text = self.smap._demangled.get(symbol)
            qualified = undname.qualified(text) if text else None
            if qualified:
                # Overloads differ only in their parameters: keep the list.
                match = undname._SIGNATURE.search(text)
                params = match.group(0) if match else ""
                return _pretty(qualified + params)
        return symbol if len(symbol) < 70 else symbol[:67] + "..."

    def where(self, n: int) -> str:
        loc = self.locs.get(n)
        if not loc:
            return ""
        approx = "~" if loc.get("approximate") else ""
        text = f" `{loc['text'][:70]}`" if loc.get("text") else ""
        return f" ({approx}{loc['file']}:{loc['line']}{text})"

    def site(self, n: int) -> str:
        m = self.model
        label = f"#{m.index[n]}" if m.index[n] >= 0 else f"(in #{m.anchor[n]}'s expansion)"
        text = f"{label} {self.name(m.sym[n])}{self.where(n)}"
        top = m.top_ancestor(n)
        if top != n:
            text += f", under root site #{m.index[top]} {self.name(m.sym[top])}{self.where(top)}"
        return text

    def library(self, symbol: str | None) -> bool:
        return bool(symbol and self.smap is not None and self.smap.is_library(symbol))

    def _library(self, symbol: str) -> str:
        return " [a library body: not a source lever]" if self.library(symbol) else ""

    def edit(self, edit: Edit, span: tuple | None = None, counts: tuple | None = None,
             deltas: tuple | None = None) -> str:
        m = self.model
        if edit.kind == "cb":
            cb = m.root["cb"]
            new = cb + edit.amount
            return (f"root cb {cb} -> {new} (budget {min(35000, max(1000, 2 * cb))} -> "
                    f"{min(35000, max(1000, 2 * new))})")
        if edit.kind == "callee":
            costs = sorted({c for c, s in zip(m.cost, m.sym) if s == edit.symbol})
            new = [max(1, c + edit.amount) for c in costs]
            if deltas and deltas[0] != deltas[1]:
                return (f"callee {self.name(edit.symbol)} cb {'/'.join(map(str, costs))} "
                        f"{deltas[0]:+d}..{deltas[1]:+d} at every site"
                        + self._library(edit.symbol))
            note = (f"; at {m.limit} or more it is no longer a candidate"
                    if any(c >= m.limit for c in new) else "")
            if any(c > 40 for c in new) and any(c <= 40 for c in costs):
                note += "; crosses the free limit 40"
            return (f"callee {self.name(edit.symbol)} cb {'/'.join(map(str, costs))} -> "
                    f"{'/'.join(map(str, new))} ({edit.amount:+d}) at every site{note}"
                    + self._library(edit.symbol))
        nodes = self.shapes.get(edit.owner, [])
        scope = ("" if edit.owner is None else
                 f"in {self.name(edit.owner)}'s body (every expansion"
                 f"{'; a library body' if self.library(edit.owner) else ''}): ")
        if edit.kind == "remove":
            return f"{scope}remove site {self.site(nodes[edit.position])}"
        low, high = span or (edit.position, edit.position)
        many = (f"{counts[0]}..{counts[1]}" if counts and counts[0] != counts[1]
                else str(edit.amount))
        text = f"{scope}add {many} free candidate site(s) after {self.site(nodes[low])}"
        if high > low:
            text += f", or as late as after {self.site(nodes[high])}"
        if high + 1 < len(nodes):
            text += f"; before {self.site(nodes[high + 1])}"
        else:
            text += "; at the end of the list"
        return text

    def flips(self, fit: Fit, base_expanded: list, base_vetoed: list,
              limit: int = 12) -> list[str]:
        m = self.model
        out = []
        for n, budget in fit.flips[:limit]:
            now = not (base_expanded[n] and not base_vetoed[n])
            verdict = "now expands" if now else "now stays a call"
            budget_text = (" (no longer a candidate)" if budget == "non-candidate" else
                           f" at budget {budget}" if budget is not None else "")
            out.append(f"{self.site(n)} cb {m.cost[n]}: {verdict}{budget_text}")
        if len(fit.flips) > limit:
            out.append(f"... {len(fit.flips) - limit} more decision(s)")
        return out


def model_root_label(model: Model) -> str:
    return model.root["symbol"]


def fit_json(fit: Fit, render: Renderer) -> dict:
    return dict(edits=[dict(kind=e.kind, owner=e.owner, position=e.position,
                            symbol=e.symbol, amount=e.amount,
                            text=render.edit(e, fit.span, fit.counts, fit.deltas))
                       for e in fit.edits],
                distance=fit.distance, incomplete=fit.incomplete,
                cb_window=fit.window, insert_span=fit.span, insert_counts=fit.counts,
                callee_deltas=fit.deltas, library=fit.library, trivial=fit.trivial,
                veto_estimated=fit.estimated,
                flips=[dict(index=render.model.index[n], anchor=render.model.anchor[n],
                            symbol=render.model.sym[n], budget=b) for n, b in fit.flips])


def print_solution(root: dict, wanted: dict, search: Search, result: dict,
                   render: Renderer, write=print) -> None:
    m = search.model
    write(f"{root['symbol']} cb={root['cb']} budget={root['initial_budget']}"
          f" ({len(root['sites'])} traced sites, {len(m.sym)} with template children)")
    for symbol, delta in sorted(wanted.items()):
        write(f"  want {render.name(symbol)}: {'+' if delta > 0 else ''}{delta} call(s)")
    if not m.reproduces():
        write("  [warning] the replay does not reproduce this root's trace; "
              "hypotheses are unreliable")
    if result["base_distance"] == 0:
        write("  the replay already gives retail's counts")
        return
    state = (f"{result['evaluated']} replays" +
             ("; TIME LIMIT reached, the search is partial" if result.get("timed_out") else ""))
    if result["fits"]:
        write(f"  {result['total_fits']} fitting hypothesis set(s) ({state}); best first:")
        for rank, fit in enumerate(result["fits"], 1):
            window = (f"holds for root cb {root['cb'] + fit.window[0]}..{root['cb'] + fit.window[1]}"
                      if fit.window else "cb window unknown")
            extra = (" [incomplete: a newly expanded site's children are unknown]"
                     if fit.incomplete else "") + (
                " [veto estimated from an untraced children pattern]" if fit.estimated else "")
            write(f"  {rank}. ({window}){extra}")
            for edit in fit.edits:
                write("       - " + render.edit(edit, fit.span, fit.counts, fit.deltas))
            for line in render.flips(fit, search.base_expanded, search.base_vetoed, 8):
                write("         " + line)
        return
    write(f"  no fit with up to the searched edits ({state}); nearest:")
    for rank, fit in enumerate(result["best"][:5], 1):
        write(f"  {rank}. {fit.distance} call(s) still differ")
        for edit in fit.edits:
            write("       - " + render.edit(edit, fit.span, fit.counts, fit.deltas))
        for line in render.flips(fit, search.base_expanded, search.base_vetoed, 6):
            write("         " + line)
    pieces = result.get("partial") or {}
    if pieces:
        write("  per callee (edits giving exactly that callee's change, moving no "
              "other callee away from retail):")
    for symbol, fits in pieces.items():
        write(f"    {render.name(symbol)} {wanted[symbol]:+d}:"
              + ("" if fits else " no single or paired edit"))
        for fit in fits[:3]:
            write("      * " + "; ".join(render.edit(e, fit.span, fit.counts, fit.deltas)
                                        for e in fit.edits))
