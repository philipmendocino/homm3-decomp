"""Close census gaps in statically linked library code with reviewed rows.

    homm3 --image h3maped verify library-closure          proposals and refusals
    homm3 --image h3maped verify library-closure --write  append the proposals
                                                          to runtime-contributions.tsv

A gap is a census function that no source claims and whose nearest owned
neighbours on both sides are library code. The linker places every library
member after the project's objects, and each member's sections together, so
such a body is a library section unless identical-code folding moved a
project body there (a folded body is its claimant's, so it is no gap).

A candidate is an unplaced code section of a pinned archive member whose
masked bytes start an unowned stretch of the gap. It is refused before
verification when

* the gap's address is already named, by the runtime map or by a slot of a
  library vtable at the address the image's RTTI gives it, and the section
  defines none of those names;
* a vtable it stores is not the address the image's RTTI gives that class;
* a relocation reaches a symbol the image already names otherwise, unless
  the library body of that symbol is byte-identical there (a fold);
* a datum it references has other bytes, strings or code pointers there.

Each survivor is offered with its companions: the unplaced sections of the
same member that its relocations reach (EH handler stubs, local callees),
placed where the retail field points; as `alias` rows where another owner
already holds those bytes. A group is admitted only when
`library_code.verify`, over every reviewed row plus the round, finds each of
its rows exact; the admitted rows are proved again together after each
round, and a gap keeps its rows only when they cover all of it up to linker
fill. When sections of different symbols verify at one gap, the bytes are an
identical-code fold of all of them: the row lists the adjacent member's
section (link order) and its evidence counts the others.
"""

from __future__ import annotations

import bisect
import re
import struct
from collections import defaultdict
from dataclasses import dataclass, field

from homm3.verify import library_code as lc

VTABLE = re.compile(r"^\?\?_7(.+)@@6B@$")
DELETING = re.compile(r"^\?\?_[GE](.+)@@[QUI]AEPAXI@Z$")
FILL = 0xCC
#: Linker fill between library sections never reaches a 16-byte boundary.
MAX_FILL = 16
MAX_ROUNDS = 400
#: A candidate refused only for a target no round has placed yet is offered
#: again this often, after every untried candidate of its gap.
RETRIES = 3
#: Rounds without an admission before the fixpoint stops.
PATIENCE = 6
EVIDENCE = 'complete pinned native section: every byte, named relocation and reviewed DIR32 site verifies'


@dataclass
class Gap:
    rva: int
    size: int
    before: str
    after: str
    rows: list = field(default_factory=list)       # admitted (row, section)
    refused: list = field(default_factory=list)    # (section key, reason)

    @property
    def end(self):
        return self.rva + self.size


def section_symbol(sec) -> str:
    """The name a reviewed row gives a section: its COMDAT symbol, else the
    first named symbol it defines (public before static at one offset)."""
    if sec.comdat:
        return sec.comdat
    named = sorted((s.value, s.storage_class != 2, s.name) for s in sec.obj.symbols.values()
                   if s.section == sec.number and s.storage_class in (2, 3)
                   and not s.name.startswith(('.', '$S', '$T')))
    return named[0][2] if named else '-'


class Closure:
    def __init__(self, pe, model, ledger_rvas, category, sizes, runtime_map, aliases,
                 vtables, rows=None, library=None):
        from homm3.core import msvc_names
        from homm3.core.tsv import read
        from homm3.delink.image import Image as DelinkImage
        from homm3.retail_labels.censuses import functions
        self.mask = msvc_names.mask
        self.pe, self.base = pe, pe.image_base
        self.image = lc.Image(pe)
        zlib = read(lc.ZLIB_MAP)[2]
        self.zlib_rows = [(int(r['rva'], 16), r['name'], r['unit'])
                          for r in zlib if r.get('kind', 'func') == 'func']
        self.lib = library or lc.load_libraries(zlib_units=[u for _r, _n, u in self.zlib_rows])
        self.names = defaultdict(set)
        for b in model.functions + model.data:
            for entry in (b, *b.aliases):
                if entry.name and entry.channel and entry.channel != 'functions_static_libs':
                    self.names[self.mask(entry.name)].add(b.rva)
        self.game = lc.GameComdats(pe, [r['rva'] for r in functions()],
                                   exclude={u for _r, _n, u in self.zlib_rows})
        self.sites = DelinkImage(pe).reloc_sites
        self.rows = lc.read_inventory() if rows is None else rows
        self.category, self.sizes, self.claimed = category, sizes, set(ledger_rvas)
        self.vt = vtables
        self.vt_at = {rva: cls for cls, rva in vtables.items()}
        self.known = defaultdict(set)              # rva -> masked names
        self.where = defaultdict(set)              # masked name -> rvas
        for name, rvas in self.names.items():
            for rva in rvas:
                self.name(rva, name)
        for rva, name in list(runtime_map) + list(aliases):
            self.name(rva, self.mask(name))
        self.slot_classes = defaultdict(set)
        self._name_vtable_slots()
        self.placed = {(r.library, r.member, r.section) for r in self.rows if r.section}
        self.owned = sorted((r.rva, r.rva + r.size) for r in self.rows if r.kind == 'code')
        for r in self.rows:
            if r.kind == 'code':
                self._learn(r)
        self.by_size = defaultdict(list)
        for (library, member), obj in self.lib.objects.items():
            if library == 'zlib':
                continue
            for number, s in enumerate(obj.sections, start=1):
                if s.characteristics & lc.MEM_EXECUTE and (library, member, number) not in self.placed:
                    sec = self.lib.section(library, member, number)
                    if sec.size:
                        self.by_size[sec.size].append(sec)
        self.sizes_desc = sorted(self.by_size, reverse=True)
        self._starts = sorted(sizes)
        self._base_names = ({k: set(v) for k, v in self.known.items()},
                            {k: set(v) for k, v in self.where.items()})

    def _relearn(self, admitted):
        """Forget the names of rows a later proof dropped."""
        known, where = self._base_names
        self.known = defaultdict(set, {k: set(v) for k, v in known.items()})
        self.where = defaultdict(set, {k: set(v) for k, v in where.items()})
        for r, _s, _g in admitted:
            if r.kind == 'code':
                self._learn(r)

    # -- names the image already gives ------------------------------------
    def _name_vtable_slots(self):
        """Each slot of a library vtable at its RTTI address names the
        virtual function the retail pointer reaches."""
        for cls, vrva in self.vt.items():
            for library, member, number, value in self.lib.public.get(f'??_7{cls}@@6B@', ()):
                if library == 'zlib':
                    continue
                d = self.lib.section(library, member, number)
                if d.code:
                    continue
                retail = self.pe.read(vrva - value, len(d.data))
                if lc.differences(d, retail):
                    continue
                for off, typ, index in d.relocations:
                    sym = d.obj.symbols[index]
                    if typ != lc.DIR32 or off < value or sym.name.startswith('??_R4'):
                        continue
                    target = self._field(d.data, retail, off) - self.base
                    self.name(target, self.mask(sym.name))
                    # a slot names the vector deleting destructor; VC6 emits
                    # the scalar one's body for it
                    if sym.name.startswith('??_E'):
                        self.name(target, self.mask('??_G' + sym.name[4:]))
                    self.slot_classes[target].add(cls)
                break

    def name(self, rva, masked):
        self.known[rva].add(masked)
        self.where[masked].add(rva)

    def _learn(self, row):
        sec = self.lib.section(row.library, row.member, row.section)
        for s in sec.obj.symbols.values():
            if s.section == row.section and s.storage_class == 2:
                self.name(row.rva + s.value, self.mask(s.name))

    @staticmethod
    def _field(data, retail, off):
        return (struct.unpack_from('<I', retail, off)[0] - struct.unpack_from('<i', data, off)[0]) & 0xffffffff

    def _target(self, sec, retail, rva, off, typ):
        if typ == lc.REL32:
            word = struct.unpack_from('<i', retail, off)[0]
            return rva + off + 4 + word - struct.unpack_from('<i', sec.data, off)[0]
        return self._field(sec.data, retail, off) - self.base

    def _definitions(self, sec, sym):
        """(section, offset) bodies defining a relocation's symbol."""
        if sym.section > 0:
            return [(self.lib.section(sec.library, sec.member, sym.section), sym.value)]
        return [(self.lib.section(l, m, n), v) for l, m, n, v in self.lib.public.get(sym.name, ())
                if l != 'zlib']

    def _identical_at(self, defs, target, code):
        return any(d.code == code and d.size and not lc.differences(d, self.pe.read(target - v, d.size))
                   for d, v in defs)

    # -- refusals ---------------------------------------------------------
    def _datum_contradiction(self, d, value, address, depth=0):
        lo = address - value
        if d.bss or lo < 0:
            return None
        retail = self.pe.read(lo, len(d.data))
        if lc.differences(d, retail):
            return f'datum bytes at {lo + self.base:#x} differ'
        for off, typ, index in d.relocations:
            if typ != lc.DIR32 or off + 4 > len(d.data):
                continue
            sym = d.obj.symbols[index]
            target = self._field(d.data, retail, off) - self.base
            defs = self._definitions(d, sym)
            if sym.name.startswith('??_C@'):        # a string literal
                if defs and not self._identical_at(defs, target, False):
                    return f'{sym.name} string at {target + self.base:#x} differs'
                continue
            code = [x for x in defs if x[0].code]
            if code:
                names = {self.mask(s.name) for c, v in code for s in c.obj.symbols.values()
                         if s.section == c.number and s.value == v and s.storage_class == 2} \
                    or {self.mask(sym.name)}
                known = self.known.get(target)
                if known and not names & known and not self._identical_at(code, target, True):
                    return f'datum field {sorted(names)[0]}: {target + self.base:#x} is {sorted(known)[0]}'
                continue
            if depth < 1 and defs and not self.known.get(target):
                why = self._datum_contradiction(defs[0][0], defs[0][1], target, depth + 1)
                if why:
                    return why
        return None

    def refusal(self, sec, rva):
        own = {self.mask(s.name) for s in sec.obj.symbols.values()
               if s.section == sec.number and s.value == 0 and s.storage_class == 2}
        if own and self.known.get(rva) and not own & self.known[rva]:
            return f'{rva + self.base:#x} is {sorted(self.known[rva])[0]}'
        # a public symbol has one address: the linker keeps one COMDAT copy
        for s in sec.obj.symbols.values():
            if s.section == sec.number and s.storage_class == 2:
                elsewhere = self.where.get(self.mask(s.name), set()) - {rva + s.value}
                if elsewhere and rva + s.value not in self.where[self.mask(s.name)]:
                    return f'{s.name} is at {min(elsewhere) + self.base:#x}'
        if sec.comdat and DELETING.match(sec.comdat) and DELETING.match(sec.comdat).group(1) not in self.vt:
            return 'deleting destructor of a class without an RTTI vtable'
        retail = self.pe.read(rva, sec.size)
        for off, typ, index in sec.relocations:
            if typ not in (lc.DIR32, lc.REL32) or off + 4 > sec.size:
                continue
            sym = sec.obj.symbols[index]
            target = self._target(sec, retail, rva, off, typ)
            # a member-local code section can only sit where it is reached
            if sym.section > 0 and sym.section != sec.number:
                local_sec = self.lib.section(sec.library, sec.member, sym.section)
                if local_sec.code and not (sym.storage_class == 2 and local_sec.comdat):
                    lo = target - sym.value
                    if lo < 0 or lc.differences(local_sec, self.pe.read(lo, local_sec.size)):
                        return f'{sym.name}: its section does not match at {target + self.base:#x}'
            m = VTABLE.match(sym.name)
            if m:
                if self.vt.get(m.group(1)) != target:
                    return f'{sym.name} is not the RTTI vtable at {target + self.base:#x}'
                continue
            if target in self.vt_at:
                return f'{sym.name} at the RTTI vtable of {self.vt_at[target]}'
            if sym.name.startswith('__imp_'):
                continue
            defs = self._definitions(sec, sym)
            if typ == lc.DIR32:
                data = [x for x in defs if not x[0].code]
                if data:
                    why = self._datum_contradiction(data[0][0], data[0][1], target)
                    if why:
                        return f'{sym.name}: {why}'
            local = sym.section > 0 and not (sym.storage_class == 2 and defs[0][0].comdat)
            known = self.known.get(target)
            if local:
                names = {self.mask(s.name) for s in defs[0][0].obj.symbols.values()
                         if s.section == sym.section and s.value == sym.value and s.storage_class == 2}
            else:
                names = {self.mask(sym.name)}
            if known and names and not names & known \
                    and not self._identical_at([x for x in defs if x[0].code], target, True):
                return f'{sym.name}: {target + self.base:#x} is {sorted(known)[0]}'
        return None

    # -- candidates and groups -------------------------------------------
    def covered_end(self, pos, extra):
        """The end of an owned row covering `pos`, else None."""
        spans = self.owned + sorted((r.rva, r.rva + r.size) for r, _s in extra if r.kind == 'code')
        for lo, hi in spans:
            if lo <= pos < hi:
                return hi
        return None

    def open_position(self, gap, extra):
        pos = gap.rva
        while pos < gap.end:
            hi = self.covered_end(pos, extra)
            if hi is not None:
                pos = hi
                continue
            fill = pos
            while fill < gap.end and self.pe.read(fill, 1)[0] == FILL:
                fill += 1
            if fill > pos and (fill == gap.end or self.covered_end(fill, extra) is not None
                               or fill % 4 == 0) and fill - pos < MAX_FILL:
                pos = fill
                continue
            return pos
        return None

    def candidates(self, pos, end, near=()):
        """Sections matching at `pos`: the longest first, the adjacent members' first."""
        out = []
        for size in self.sizes_desc:
            if size > end - pos:
                continue
            matches = [sec for sec in self.by_size[size]
                       if pos % sec.align == 0 and not lc.differences(sec, self.pe.read(pos, size))]
            out += sorted(matches, key=lambda sec: sec.member not in near)
        return out

    def publics(self, group):
        return {self.mask(s.name) for _r, sec in group for s in sec.obj.symbols.values()
                if s.section == sec.number and s.storage_class == 2}

    def companions(self, sec, rva, used):
        """Rows for unplaced sections of `sec`'s member its relocations reach."""
        out, todo = [], [(sec, rva)]
        while todo:
            s, at = todo.pop()
            retail = self.pe.read(at, s.size)
            for off, typ, index in s.relocations:
                sym = s.obj.symbols[index]
                if sym.section <= 0 or typ not in (lc.DIR32, lc.REL32) or off + 4 > s.size:
                    continue
                target_sec = self.lib.section(s.library, s.member, sym.section)
                key = (s.library, s.member, sym.section)
                if not target_sec.code or key in used or key in self.placed:
                    continue
                target = self._target(s, retail, at, off, typ)
                # A public COMDAT resolves by name; the image keeps one copy,
                # so offer this member's only where no other address has it,
                # and not where that name is already known (a game TU's copy).
                if sym.storage_class == 2 and target_sec.comdat \
                        and (self.where.get(self.mask(sym.name), set()) - {target}
                             or self.mask(sym.name) in self.known.get(target, ())):
                    continue
                lo = target - sym.value
                if lo < 0 or lc.differences(target_sec, self.pe.read(lo, target_sec.size)):
                    continue
                used.add(key)
                kind = 'alias' if self._held(lo, lo + target_sec.size) else 'code'
                out.append((lc.Contribution(lo, target_sec.size, s.library, s.member, sym.section,
                                            section_symbol(target_sec), '-', kind), target_sec))
                if kind == 'code':
                    todo.append((target_sec, lo))
        return out

    def _held(self, lo, hi):
        """Whether another owner (a claimed body or a reviewed section) holds bytes of [lo, hi)."""
        i = bisect.bisect_right(self._starts, hi - 1) - 1
        while i >= 0 and self._starts[i] + self.sizes[self._starts[i]] > lo:
            start = self._starts[i]
            if start in self.claimed:
                return True
            i -= 1
        return any(a < hi and lo < b for a, b in self.owned)

    def verify(self, extra):
        verdicts, _data = lc.verify(self.pe, self.rows + extra, names=self.names, library=self.lib,
                                    zlib_rows=self.zlib_rows, game_comdats=self.game,
                                    image=self.image, reloc_sites=self.sites)
        return {(v.row.rva, v.row.library, v.row.member, v.row.section, v.row.kind): v
                for v in verdicts}

    @staticmethod
    def key(row):
        return (row.rva, row.library, row.member, row.section, row.kind)

    # -- the fixpoint ----------------------------------------------------
    def ambiguous_bytes(self, sec, pos, cands):
        """A relocation-free body several sections share and nothing names here."""
        if sec.relocations or len(cands) < 2:
            return False
        own = {self.mask(s.name) for s in sec.obj.symbols.values()
               if s.section == sec.number and s.value == 0 and s.storage_class == 2}
        return not (own & self.known.get(pos, set()))

    def run(self, gaps, log=lambda _m: None):
        """Admit in two phases: first everything provable by relocations or
        names, so that the sections those bodies reach are placed where they
        reach them; then the relocation-free folds no one names."""
        state = (defaultdict(set), defaultdict(dict), [])
        for trivial in (False, True):
            self._rounds(gaps, state, trivial, log)
        return self._complete(gaps, state[2])

    def _rounds(self, gaps, state, trivial, log):
        tried, deferred, admitted = state         # (gap, pos) -> keys; -> {key: retries}; rows
        idle = 0
        for rnd in range(1, MAX_ROUNDS + 1):
            extra = [(r, s) for r, s, _g in admitted]
            used = self.placed | {(r.library, r.member, r.section) for r, _s in extra}
            groups, taken, names = [], set(), set()
            for gap in gaps:
                pos = self.open_position(gap, extra)
                if pos is None:
                    continue
                at = (gap.rva, pos)
                cands = self.candidates(pos, gap.end, (gap.before, gap.after))
                order = [c for c in cands if (c.library, c.member, c.number) not in tried[at]] + \
                    [c for c in cands if deferred[at].get((c.library, c.member, c.number), RETRIES) < RETRIES]
                for sec in order:
                    k = (sec.library, sec.member, sec.number)
                    if k in used or k in taken or not trivial and self.ambiguous_bytes(sec, pos, cands):
                        continue
                    if k not in tried[at]:
                        tried[at].add(k)
                        why = self.refusal(sec, pos)
                        if why:
                            gap.refused.append((k, why))
                            continue
                    else:
                        deferred[at][k] += 1
                    main = lc.Contribution(pos, sec.size, sec.library, sec.member, sec.number,
                                           section_symbol(sec), '-', 'code')
                    group = [(main, sec)] + self.companions(sec, pos, used | taken | {k})
                    # pick-any copies of one public symbol in other members:
                    # one address per name, also within a round
                    if self.publics(group) & names:
                        tried[at].discard(k)          # offer it again next round
                        continue
                    names |= self.publics(group)
                    taken |= {(r.library, r.member, r.section) for r, _s in group}
                    groups.append((gap, at, k, group))
                    break
            if not groups:
                break
            verdicts = self.verify([r for r, _s in extra] + [r for *_x, grp in groups for r, _s in grp])
            new = 0
            for gap, at, k, group in groups:
                bad = [v for r, _s in group if (v := verdicts[self.key(r)]).verdict != 'exact']
                if bad and all('bases' in why for v in bad for why in v.reasons):
                    # another offer of this round implied the datum elsewhere:
                    # prove the group alone
                    alone = self.verify([r for r, _s, _g in admitted] + [r for r, _s in group])
                    bad = [v for r, _s in group if (v := alone[self.key(r)]).verdict != 'exact']
                if bad:
                    gap.refused.append((k, '; '.join(bad[0].reasons[:2])))
                    # a dependency may resolve once a later round admits it
                    if any('placed' in why for v in bad for why in v.reasons):
                        deferred[at].setdefault(k, 0)
                    continue
                admitted.extend((r, s, gap) for r, s in group)
                for r, _s in group:
                    if r.kind == 'code':
                        self._learn(r)
                new += 1
            # prove the admitted rows again without this round's refusals
            while True:
                verdicts = self.verify([r for r, _s, _g in admitted])
                drop = {id(g) for r, _s, g in admitted if verdicts[self.key(r)].verdict != 'exact'}
                if not drop:
                    break
                admitted[:] = [(r, s, g) for r, s, g in admitted if id(g) not in drop]
                self._relearn(admitted)
            log(f'[library-closure] {"fold" if trivial else "proof"} round {rnd}: {len(groups)} offered, '
                f'{new} admitted, {len(admitted)} rows')
            idle = 0 if new else idle + 1
            if idle >= PATIENCE:
                break

    def _complete(self, gaps, admitted):
        """A gap keeps its rows only when they cover it completely."""
        extra = [(r, s) for r, s, _g in admitted]
        complete = {id(g) for g in gaps if self.open_position(g, extra) is None}
        # one address per public name across every reviewed row
        seen = {}
        for row in self.rows:
            if row.kind == 'code':
                sec = self.lib.section(row.library, row.member, row.section)
                for name in self.publics([(row, sec)]):
                    seen.setdefault(name, row.rva)
        for r, s, g in admitted:
            for name in self.publics([(r, s)]):
                if seen.setdefault(name, r.rva) != r.rva and r.kind == 'code':
                    complete.discard(id(g))
        while True:
            kept = [(r, s, g) for r, s, g in admitted if id(g) in complete]
            verdicts = self.verify([r for r, _s, _g in kept])
            drop = {id(g) for r, _s, g in kept if verdicts[self.key(r)].verdict != 'exact'}
            if not drop:
                break
            complete -= drop
        for gap in gaps:
            gap.rows = [(r, s) for r, s, g in kept if g is gap]
        return kept

    def alternatives(self, admitted):
        """Other sections that verify where an admitted row's section does."""
        rows = [r for r, _s, _g in admitted]
        used = self.placed | {(r.library, r.member, r.section) for r in rows}
        pending = {}
        for r, s, g in admitted:
            if r.kind == 'code':
                pending[self.key(r)] = [c for c in self.by_size[s.size]
                                        if c is not s and (c.library, c.member, c.number) not in used
                                        and r.rva % c.align == 0
                                        and not lc.differences(c, self.pe.read(r.rva, c.size))
                                        and not self.refusal(c, r.rva)]
        exact = defaultdict(list)
        while any(pending.values()):
            trial, offered, taken = [], [], set()
            for k, lst in pending.items():
                while lst:
                    c = lst.pop()
                    ck = (c.library, c.member, c.number)
                    if ck not in taken:
                        taken.add(ck)
                        row = lc.Contribution(k[0], c.size, c.library, c.member, c.number,
                                              section_symbol(c), '-', 'code')
                        trial.append(row)
                        offered.append((k, row))
                        break
            if not trial:
                break
            verdicts = self.verify(rows + trial)
            for k, row in offered:
                if verdicts[self.key(row)].verdict == 'exact':
                    exact[k].append(row)
        return exact


def gaps_of(category, sizes, claimed, member_of, folded=None):
    """Census functions no source claims between library neighbours.

    `folded` maps a claimed body that is also a reviewed library alias (an
    identical-code fold such as an inline constructor sharing `CString`'s
    bytes) to its member: it bounds a gap as library code does."""
    folded = folded or {}
    starts = sorted(sizes)
    skip = ('eh-funclet', 'init-thunk', 'import-thunk')

    def neighbour(i, step):
        i += step
        while 0 <= i < len(starts):
            rva = starts[i]
            c = category.get(rva)
            if rva in folded:
                return folded[rva]
            if rva in claimed:
                return None
            if c == 'runtime':
                return member_of(rva)
            if c == 'target' or c in skip:
                i += step
                continue
            return None                           # zlib: project-compiled
        return None

    out = []
    for i, rva in enumerate(starts):
        if category.get(rva) != 'target' or rva in claimed:
            continue
        before, after = neighbour(i, -1), neighbour(i, 1)
        if before and after:
            out.append(Gap(rva, sizes[rva], before, after))
    return out


def evidence(row, gap, alternatives, slot_classes, main):
    if row.kind == 'alias':
        return (f'folded copy reached from {main.symbol} at {main.rva:#x}; '
                f'pinned native section bytes and named relocations verify; no additional body ownership')
    if row is not main:
        return f'{EVIDENCE}; reached from {main.symbol} at {main.rva:#x}'
    where = f'census body between {gap.before} and {gap.after}'
    slots = sorted(slot_classes.get(row.rva, ()))
    via = f'; the {slots[0]} vtable slot reaches it' if slots else ''
    others = sorted({a.symbol for a in alternatives if a.symbol != row.symbol})
    if others:
        return (f'{EVIDENCE}; identical code: {len(others)} other pinned section(s) verify here '
                f'(e.g. {others[0]}); {where}, the adjacent member\'s copy{via}')
    return f'{EVIDENCE}; the only pinned section that does; {where}{via}'


def main(argv=None) -> int:
    import argparse
    from homm3.core.pe import image
    from homm3.core.tsv import read
    from homm3.match import status, universe
    from homm3.model import resolve
    parser = argparse.ArgumentParser(prog='homm3 verify library-closure', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--write', action='store_true', help='append the proposed rows')
    parser.add_argument('--refusals', action='store_true', help='list every open gap and why')
    args = parser.parse_args(argv)

    category, sizes = universe.classify()
    claimed = set(status.ledger_bodies(status.load_baseline()))
    runtime_map = [(int(r['rva'], 16), r['name']) for r in read(universe.RUNTIME_MAP)[2]]
    alias_path = universe.RUNTIME_MAP.parent / 'runtime-aliases.tsv'
    aliases = [(int(r['rva'], 16), r['name']) for r in read(alias_path)[2]] if alias_path.is_file() else []
    vtables = {r['class']: int(r['rva'], 16) for r in read(universe.RUNTIME_MAP.parent / 'vtables.tsv')[2]}
    members = {int(r['rva'], 16): r['member'] for r in read(universe.RUNTIME_MAP)[2]}
    contributions = sorted((int(r['rva'], 16), int(r['size'], 0), r['member'])
                           for r in universe._runtime_contributions() if r['kind'] == 'code')

    def member_of(rva):
        if rva in members:
            return members[rva]
        i = bisect.bisect_right(contributions, (rva, 1 << 30, '')) - 1
        if i >= 0 and contributions[i][0] <= rva < contributions[i][0] + contributions[i][1]:
            return contributions[i][2]
        return 'library'

    folded = {int(r['rva'], 16): r['member'] for r in universe._runtime_contributions()
              if r['kind'] == 'alias'}
    gaps = gaps_of(category, sizes, claimed, member_of, folded)
    closure = Closure(image(), resolve(), claimed, category, sizes, runtime_map, aliases, vtables)
    admitted = closure.run(gaps, log=print)
    alternatives = closure.alternatives(admitted)

    # an identical-code fold lists the adjacent member's verifying section
    final = []
    for gap in gaps:
        if not gap.rows:
            continue
        lead = gap.rows[0][0]
        for row, _sec in gap.rows:
            alts = alternatives.get(Closure.key(row), [])
            if len(gap.rows) == 1 and alts and row.member not in (gap.before, gap.after):
                near = [a for a in alts if a.member in (gap.before, gap.after)]
                if near:
                    alts = [row] + [a for a in alts if a is not near[0]]
                    row = lead = near[0]
            final.append((row, evidence(row, gap, alts, closure.slot_classes, lead)))
    # a gap may be closed by rows another gap's body reached
    kept = [(r, s) for r, s, _g in admitted]
    closed = [g for g in gaps if closure.open_position(g, kept) is None]
    print(f'[library-closure] {len(gaps)} gap(s), {sum(g.size for g in gaps):,} B; '
          f'{len(closed)} closed ({sum(g.size for g in closed):,} B) by {len(final)} row(s)')
    if args.refusals:
        for gap in gaps:
            if gap not in closed:
                # the adjacent members' candidates first: the likeliest owners
                refused = sorted(gap.refused, key=lambda r: r[0][1] not in (gap.before, gap.after))
                print(f'  open {gap.rva + closure.base:#x} +{gap.size} ({gap.before} .. {gap.after}): '
                      + ('; '.join(f'{k[1]}#{k[2]} {why}' for k, why in refused[:2])
                         or 'no pinned section matches its bytes'))
    if args.write and final:
        path = universe.RUNTIME_CONTRIBUTIONS
        banner, header, rows = read(path)
        for row, why in final:
            rows.append({'rva': f'{row.rva:#x}', 'size': str(row.size), 'kind': row.kind,
                         'library': row.library, 'member': row.member, 'section': str(row.section),
                         'symbol': row.symbol, 'evidence': why,
                         'ownership': 'whole-section' if row.kind == 'code' else '-'})
        from homm3.core.tsv import write
        write(path, banner, header, rows)
        print(f'[library-closure] appended {len(final)} row(s) to {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
