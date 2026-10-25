"""homm3.vc6.inline_targets - retail's retained calls as per-site targets.

`predict-inline --tu UNIT --solve` needs, per traced root, the change in
retained out-of-line calls that retail shows. Counting callee names alone
miscounts two ways:

- **ICF twins.** Retail's /OPT:ICF folded byte-identical template instances
  into one body with one label, so retail's calls to
  `vector<type_treasure_def *>::insert` read `vector<widget *>::insert`
  and every one would count as a -1/+1 pair no budget edit can fix.
- **Unlabelled retail callees.** A delinked target names a function with no
  current placement by a working label (`fn_2df99`); a stale label after a
  placement was dropped and re-added hides the callee it really is.

This module compares the root's call sites instead: our object's and
retail's REL32 references in instruction order, aligned as sequences of
callee *classes*. Two names are one class when retail cannot tell them
apart:

- the normalizer already named our reference by retail's label at that
  site (`normalize_objs`: ICF twin and address-identity retargets, read as
  the difference between the raw and the normalized candidate object);
- both names resolve to one retail address (`symbol_names.csv`,
  `address_identities.tsv`, a `fn_XXXXX` label's own address);
- the bodies are ICF-identical (`normalize_objs._icf_identical`: equal bytes
  outside relocation fields and equal relocation sites) and every relocation
  referent pair is the same name, the same retail address, or recursively
  such a twin (what /OPT:ICF folds, applied transitively the way the folded
  retail image shows it). Our body of the retail label is used when retail's
  own delinked body is unavailable.

Bodies that differ are different callees even when one retail label covers
several of our names elsewhere. The wanted change per class is the aligned
difference (retail minus ours) over the classes of traced callees; differing
sites are kept with their offsets, so the solver can also ask whether a fit
changes the calls in the places retail differs (`SiteTarget`).
"""
from __future__ import annotations

import bisect
from collections import Counter
from dataclasses import dataclass, field
import difflib
from pathlib import Path
import re

REL32 = 0x14

#: Retail names our compiler could never spell: working labels (fn_XXXXX)
#: and carve names of functions without a placement.
_SPELLABLE = re.compile(r"^[?_@$]")


def unlabelled(name: str) -> bool:
    """A retail callee left as a working label (`fn_2df99`, a carve name)."""
    return not _SPELLABLE.match(name)


# -- object reading -------------------------------------------------------

def call_sequences(path: Path) -> dict[str, list[tuple[int, str]]]:
    """Function -> [(function offset, REL32 referent)] in site order."""
    from homm3.build import canonicalize_data_symbols as canon
    coff = canon.CoffObject(Path(path).read_bytes())
    ranges = canon._function_ranges(coff)
    out: dict[str, list[tuple[int, str]]] = {}
    for relocation in sorted(coff.relocations, key=lambda r: (r.section, r.site)):
        if relocation.typ != REL32:
            continue
        owner = canon._function_owner(ranges, relocation.section, relocation.site)
        if owner is not None:
            out.setdefault(owner.name, []).append(
                (relocation.site - owner.value, coff.symbols[relocation.symbol_index].name))
    return out


@dataclass(frozen=True)
class Body:
    """A function's bytes, its relocation sites (function-relative) and
    each site's (type, referent, whether the referent is external)."""
    payload: bytes
    sites: tuple[int, ...]
    referents: tuple[tuple[int, str, bool], ...]


def object_bodies(path: Path) -> dict[str, Body]:
    """Every uniquely defined function body of one object, with referents
    (normalize_objs keeps them only for foldable COMDAT functions; the
    delinked retail objects have none of those)."""
    from homm3.build import canonicalize_data_symbols as canon, normalize_objs
    try:
        coff = canon.CoffObject(Path(path).read_bytes())
    except (OSError, ValueError):
        return {}
    by_section: dict[int, list] = {}
    for relocation in coff.relocations:
        by_section.setdefault(relocation.section, []).append(relocation)
    out: dict[str, Body | None] = {}
    for section_index, ranges in canon._function_ranges(coff).items():
        data = coff.section_bytes(coff.sections[section_index - 1])
        relocations = sorted(by_section.get(section_index, ()), key=lambda r: r.site)
        for start, end, symbol in ranges:
            own = [r for r in relocations if start <= r.site < end]
            referents = []
            for r in own:
                target = coff.symbols[r.symbol_index]
                referents.append((r.typ, target.name,
                                  target.storage_class == normalize_objs.EXTERNAL_STORAGE))
            body = Body(normalize_objs._unpadded_icf_payload(
                            Body(data[start:end], tuple(r.site - start for r in own), ())),
                        tuple(r.site - start for r in own), tuple(referents))
            name = symbol.name
            out[name] = None if name in out and out[name] != body else body
    return {name: body for name, body in out.items() if body is not None}


class BodyIndex:
    """Name -> Body over a set of objects, read lazily: the objects' symbol
    tables first (which object defines a name), a body only when asked.
    `first` objects win; a name two other objects define differently is
    ambiguous (None)."""

    def __init__(self, paths, first=()):
        self.first = [Path(p) for p in first]
        self.paths = [Path(p) for p in paths]
        self._bodies: dict[Path, dict[str, Body]] = {}
        self._where: dict[str, list[Path]] | None = None

    def _definers(self) -> dict[str, list[Path]]:
        if self._where is None:
            from homm3.build import canonicalize_data_symbols as canon
            where: dict[str, list[Path]] = {}
            for path in self.paths:
                try:
                    coff = canon.CoffObject(path.read_bytes())
                except (OSError, ValueError):
                    continue
                for ranges in canon._function_ranges(coff).values():
                    for _start, _end, symbol in ranges:
                        where.setdefault(symbol.name, []).append(path)
            self._where = where
        return self._where

    def _of(self, path: Path) -> dict[str, Body]:
        if path not in self._bodies:
            self._bodies[path] = object_bodies(path)
        return self._bodies[path]

    def get(self, name: str) -> Body | None:
        for path in self.first:
            body = self._of(path).get(name)
            if body is not None:
                return body
        found = {self._of(path).get(name) for path in self._definers().get(name, ())}
        found.discard(None)
        return next(iter(found)) if len(found) == 1 else None

    __call__ = get


def normalizer_twins(raw: dict[str, list], normalized: dict[str, list]) -> set[tuple[str, str]]:
    """(our name, the label the normalizer gave it) at every reference the
    candidate normalization retargeted (ICF twins, proven address
    identities): the same function's references, site for site."""
    pairs = set()
    for function, rows in raw.items():
        other = normalized.get(function)
        if other is None or len(other) != len(rows):
            continue
        for (at, ours), (at2, label) in zip(rows, other):
            if at == at2 and ours != label:
                pairs.add((ours, label))
    return pairs


# -- equivalence ----------------------------------------------------------

class Equivalence:
    """Whether a retail callee label is the callee our source named.

    `ours(name)` and `retail(name)` return a `Body` or None; `address(name)`
    the one retail RVA a name is proven at, or None; `twins` the
    normalizer's (ours, label) retargets. Two names proven at different
    retail addresses are different functions (retail kept both: the
    vector<int> and vector<TRmgMapPosition> push_back copies call different
    inserts). Otherwise bodies are the same callee when they are
    ICF-identical and every referent pair is the same name, the same retail
    address, two local (non-external) symbols at equal sites, or recursively
    the same callee."""

    def __init__(self, ours, retail, address=lambda name: None, twins=(),
                 depth: int = 8, image=None):
        """`image(rva)` returns (retail bytes, image base) of the function
        at `rva`, or None: the last resort for a retail label without a
        delinked body (a working label of an unclaimed function)."""
        self.ours, self.retail, self.address = ours, retail, address
        self.twins = set(twins)
        self.depth = depth
        self.image = image
        self._memo: dict = {}

    def _addresses(self, a: str, b: str) -> bool | None:
        """True: one retail address; False: two different ones; None: unknown."""
        x, y = self.address(a), self.address(b)
        if x is None or y is None:
            return None
        return x == y

    def reason(self, mine: str, label: str) -> str | None:
        """Why `label` (a retail callee) is `mine` (ours), else None."""
        if mine == label:
            return "name"
        if (mine, label) in self.twins:
            return "normalizer twin"
        placed = self._addresses(mine, label)
        if placed is not None:
            return "retail address" if placed else None
        body = self.ours(mine)
        if body is None:
            return None
        other = self.retail(label)
        if other is not None and self._equal(body, other, 0, {}):
            return "ICF twin"
        other = self.ours(label)
        if other is not None and self._equal(body, other, 0, {}):
            return "ICF fold group"
        if self.retail(label) is None and self._in_image(body, label):
            return "retail image bytes"
        return None

    def _in_image(self, body: Body, label: str) -> bool:
        """Our body against retail's bytes at the label's address: equal
        outside relocation fields, at most linker fill beyond our length,
        and every call/address field reaching our referent's retail address
        where that is known."""
        rva = self.address(label)
        found = self.image(rva) if self.image is not None and rva is not None else None
        if not found:
            return False
        code, base = found
        n = len(body.payload)
        if len(code) < n or any(b not in b"\xcc\x90" for b in code[n:]):
            return False
        mine, theirs = bytearray(body.payload), bytearray(code[:n])
        for site, (kind, name, _external) in zip(body.sites, body.referents):
            field = int.from_bytes(code[site:site + 4], "little", signed=kind == REL32)
            reached = rva + site + 4 + field if kind == REL32 else field - base
            known = self.address(name)
            if known is not None and known != reached:
                return False
            mine[site:site + 4] = theirs[site:site + 4] = b"\0\0\0\0"
        return mine == theirs

    def _equal(self, left: Body, right: Body, depth: int, active: dict) -> bool:
        from homm3.build import normalize_objs
        if not normalize_objs._icf_identical(left, right):
            return False
        if len(left.referents) != len(right.referents):
            return False
        for (kind, name, external), (kind2, name2, external2) in zip(left.referents,
                                                                     right.referents):
            if kind != kind2:
                return False
            if name == name2 or (not external and not external2):
                continue
            if depth >= self.depth or not self._referent(name, name2, depth + 1, active):
                return False
        return True

    def _referent(self, mine: str, theirs: str, depth: int, active: dict) -> bool:
        key = (mine, theirs)
        if key in self._memo:
            return self._memo[key]
        if key in active:
            return True             # a cycle: assume equal while proving it
        if (mine, theirs) in self.twins:
            return True
        placed = self._addresses(mine, theirs)
        if placed is not None:
            return placed
        body = self.ours(mine) or self.retail(mine)
        other = self.retail(theirs) or self.ours(theirs)
        if body is None or other is None:
            self._memo[key] = False
            return False
        active[key] = True
        same = self._equal(body, other, depth, active)
        del active[key]
        self._memo[key] = same
        return same


# -- per-root targets -----------------------------------------------------

@dataclass
class SiteRow:
    """One call site where retail and ours differ, after class merging."""
    ours: str | None            # our callee (None: retail-only call)
    retail: str | None          # retail's label (None: our call only)
    ours_at: int | None         # function offsets
    retail_at: int | None
    anchor: int                 # our matched calls before this site


@dataclass
class Target:
    root: str
    group: dict[str, str] = field(default_factory=dict)   # name -> class key
    wanted: dict[str, int] = field(default_factory=dict)  # traced class -> retail - ours
    base_calls: dict[str, int] = field(default_factory=dict)
    retail_calls: dict[str, int] = field(default_factory=dict)
    rows: list[SiteRow] = field(default_factory=list)
    label_only: list[dict] = field(default_factory=list)  # merged label pairs
    unlabelled: list[dict] = field(default_factory=list)  # working labels at differing sites
    anchors: list[int] = field(default_factory=list)      # our call index per matched call
    ours_classes: list[str] = field(default_factory=list)

    def key(self, name: str) -> str:
        return self.group.get(name, name)


class _Union:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def root_target(root: str, ours: list[tuple[int, str]], retail: list[tuple[int, str]],
                traced: set[str], equivalence: Equivalence) -> Target:
    """The ICF-aware, site-aligned difference of one root's calls."""
    union = _Union()
    # Traced callees we always expand still name a retail label's body.
    our_names = sorted({n for _a, n in ours} | set(traced))
    retail_names = sorted({n for _a, n in retail})
    reasons: dict[tuple[str, str], str] = {}
    names = set(our_names) | set(retail_names)
    for name in names:
        union.find(name)
    for mine in our_names:
        for label in retail_names:
            if mine == label:
                continue
            why = equivalence.reason(mine, label)
            if why:
                union.join(mine, label)
                reasons[(mine, label)] = why
    # Our own names that are twins of one another are one class too: retail
    # can call either copy under one label.
    for i, a in enumerate(our_names):
        for b in our_names[i + 1:]:
            if union.find(a) != union.find(b) and equivalence.reason(a, b):
                union.join(a, b)
    members: dict[str, set[str]] = {}
    for name in names:
        members.setdefault(union.find(name), set()).add(name)
    calls = Counter(n for _a, n in ours)
    group = {}
    for rep, items in members.items():
        # The traced member our root calls most names the class.
        traced_items = sorted(items & traced, key=lambda n: (-calls[n], n))
        key = traced_items[0] if traced_items else rep
        for item in items:
            group[item] = key
    target = Target(root, group=group)
    a = [group[n] for _o, n in ours]
    b = [group[n] for _o, n in retail]
    target.ours_classes = a
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    matched = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                target.anchors.append(i)
                mine, label = ours[i][1], retail[j][1]
                if mine != label:
                    target.label_only.append(dict(ours=mine, retail=label,
                                                  reason=reasons.get((mine, label), "class"),
                                                  ours_at=ours[i][0], retail_at=retail[j][0]))
                matched += 1
            continue
        width = max(i2 - i1, j2 - j1)
        for k in range(width):
            i, j = i1 + k, j1 + k
            target.rows.append(SiteRow(
                ours=ours[i][1] if i < i2 else None,
                retail=retail[j][1] if j < j2 else None,
                ours_at=ours[i][0] if i < i2 else None,
                retail_at=retail[j][0] if j < j2 else None,
                anchor=matched))
    base, ret = Counter(a), Counter(b)
    traced_keys = {group.get(n, n) for n in traced}
    target.base_calls = {k: base[k] for k in traced_keys}
    target.retail_calls = {k: ret[k] for k in traced_keys}
    target.wanted = {k: ret[k] - base[k] for k in sorted(traced_keys) if ret[k] != base[k]}
    # Working labels where the names differ (before class merging).
    seen: dict[str, dict] = {}
    for row in [*target.rows, *(SiteRow(r["ours"], r["retail"], r["ours_at"], r["retail_at"], 0)
                                for r in target.label_only)]:
        if row.retail and unlabelled(row.retail):
            label = row.retail
            others = sorted(members[union.find(label)] - {label})
            key = group[label]
            entry = seen.setdefault(label, dict(
                label=label, sites=0, ours=Counter(),
                # counted as the callee its address or body proves
                resolved=(key if key != label else others[0]) if others else None))
            entry["sites"] += 1
            if row.ours:
                entry["ours"][row.ours] += 1
    target.unlabelled = [dict(v, ours=dict(v["ours"])) for v in seen.values()]
    return target


class UnitTargets:
    """Every root's target of one unit, from its build objects."""

    def __init__(self, unit: str, *, raw_base: Path, base: Path, target: Path,
                 base_objects=(), target_objects=(), address=None, image=None):
        self.unit = unit
        self.raw = call_sequences(raw_base) if Path(raw_base).is_file() else {}
        self.base = call_sequences(base)
        self.retail = call_sequences(target)
        twins = normalizer_twins(self.raw, self.base) if self.raw else set()
        if address is None:
            address = _retail_addresses(unit)
        self.equivalence = Equivalence(
            ours=BodyIndex(base_objects, first=[base]),
            retail=BodyIndex(target_objects, first=[target]),
            address=address, twins=twins, image=image)

    def for_root(self, root: dict) -> Target | None:
        name = root["symbol"]
        if name not in self.retail:
            return None
        ours = self.raw.get(name) or self.base.get(name, [])
        traced = {s["symbol"] for s in root["sites"]}
        return root_target(name, ours, self.retail[name], traced, self.equivalence)


def _retail_addresses(unit: str):
    """name -> its one proven retail RVA (symbol_names.csv labels,
    address_identities.tsv proofs, a working label's own address)."""
    from homm3.build import identity_relocations, normalize_objs
    symbol_rvas = {name: row for name, row in normalize_objs._retail_symbol_rvas().items()
                   if row[1] == "func"}
    identities = identity_relocations.load_identities(normalize_objs.ADDRESS_IDENTITIES)
    cache: dict[str, int | None] = {}

    def address(name: str) -> int | None:
        if name not in cache:
            cache[name] = identity_relocations.resolve_name(name, symbol_rvas, identities, unit)
        return cache[name]
    return address


def _retail_image():
    """rva -> (bytes, image base) of the census function there, or None."""
    import csv
    from homm3.build import normalize_objs
    from homm3.delink.image import retail
    try:
        with normalize_objs.FUNCTIONS.open(newline="") as stream:
            rows = csv.DictReader((line for line in stream if not line.startswith("#")),
                                  delimiter="\t")
            sizes = {int(row["rva"], 0): int(row["size"], 0) for row in rows}
        image = retail()
    except (OSError, KeyError, ValueError):
        return None

    def read(rva: int):
        size = sizes.get(rva)
        return (image.payload(rva, size), image.image_base) if size else None
    return read


def unit_targets(unit: str) -> UnitTargets:
    """The unit's targets from the current image's objdiff tree."""
    from homm3.sema import _asm
    return UnitTargets(unit, raw_base=_asm.BASE / f"{unit}.obj",
                       base=_asm.NORMAL_BASE / f"{unit}.obj",
                       target=_asm.NORMAL_TARGET / f"{unit}.c.obj",
                       base_objects=sorted(_asm.NORMAL_BASE.rglob("*.obj")),
                       target_objects=sorted(_asm.NORMAL_TARGET.rglob("*.c.obj")),
                       image=_retail_image())


# -- site-level check of a hypothesis ---------------------------------------

class SiteTarget:
    """Retail's differences located between anchors on the model's order.

    The model's retained calls in source order (a DFS over the site tree)
    align with our object's traced calls; anchors are matched calls retail
    also makes whose class changes nowhere in the root, so a run of equal
    calls with one difference stays one gap. Per gap the target is the
    change of each class's count. A fit is site-consistent when its own
    per-gap changes are exactly those and it keeps every anchor a call."""

    def __init__(self, target: Target, base_order: list[tuple[int, str]]):
        changed = {target.key(n) for row in target.rows for n in (row.ours, row.retail) if n}
        changed |= set(target.wanted)
        self.target = target
        # our call index -> model order position, over our traced calls
        traced = set(target.base_calls)
        model_keys = [target.key(s) for _n, s in base_order]
        mine = [i for i, k in enumerate(target.ours_classes) if k in traced]
        matcher = difflib.SequenceMatcher(a=[target.ours_classes[i] for i in mine],
                                          b=model_keys, autojunk=False)
        to_model: dict[int, int] = {}
        for block in matcher.get_matching_blocks():
            for k in range(block.size):
                to_model[mine[block.a + k]] = block.b + k
        self.anchors: list[int] = []            # model nodes, in order
        gap_of_anchor_ordinal: dict[int, int] = {}
        for ordinal, i in enumerate(target.anchors):
            if target.ours_classes[i] in changed or i not in to_model:
                continue
            gap_of_anchor_ordinal[ordinal] = len(self.anchors)
            self.anchors.append(base_order[to_model[i]][0])
        self.nodes = set(self.anchors)
        # A row's gap: the number of usable anchors before it.
        self.want: dict[int, Counter] = {}
        usable = sorted(gap_of_anchor_ordinal)
        for row in target.rows:
            gap = bisect.bisect_left(usable, row.anchor)
            change = self.want.setdefault(gap, Counter())
            if row.ours:
                change[target.key(row.ours)] -= 1
            if row.retail:
                change[target.key(row.retail)] += 1
        self.want = {g: Counter({k: v for k, v in c.items() if v and k in traced})
                     for g, c in self.want.items()}
        self.want = {g: c for g, c in self.want.items() if c}
        self.usable = bool(self.anchors)

    def gaps(self, order: list[tuple[int, str]]) -> tuple[dict[int, Counter], bool]:
        """Per gap, each class's count; and whether every anchor is kept."""
        out: dict[int, Counter] = {}
        gap = 0
        seen = 0
        for node, symbol in order:
            if node in self.nodes:
                gap += 1
                seen += 1
                continue
            out.setdefault(gap, Counter())[self.target.key(symbol)] += 1
        return out, seen == len(self.anchors)

    def check(self, base_order, order) -> tuple[bool, list[str]]:
        """(consistent, why not) for a hypothesis's retained order."""
        before, _kept = self.gaps(base_order)
        after, kept = self.gaps(order)
        problems = []
        if not kept:
            problems.append("expands a call retail keeps at a matched site")
        traced = set(self.target.base_calls)
        for gap in sorted(set(before) | set(after) | set(self.want)):
            got = Counter(after.get(gap, Counter()))
            got.subtract(before.get(gap, Counter()))
            got = Counter({k: v for k, v in got.items() if v and k in traced})
            want = self.want.get(gap, Counter())
            if got != want:
                problems.append(f"gap {gap}: changes {dict(got)} where retail changes {dict(want)}")
        return not problems, problems


def target_json(target: Target) -> dict:
    """A root target's JSON fields for `--solve --json`."""
    classes: dict[str, list[str]] = {}
    for name, key in target.group.items():
        if name != key:
            classes.setdefault(key, []).append(name)
    return dict(
        classes={k: sorted(v) for k, v in sorted(classes.items())},
        label_only=target.label_only, unlabelled=target.unlabelled,
        site_rows=[dict(ours=r.ours, retail=r.retail, ours_at=r.ours_at,
                        retail_at=r.retail_at, anchor=r.anchor) for r in target.rows])
