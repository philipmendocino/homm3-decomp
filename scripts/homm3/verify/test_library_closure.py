"""Census gaps between library neighbours and the names their rows take."""
import dataclasses
import struct
import unittest
import unittest.mock
from collections import defaultdict
from types import SimpleNamespace

from homm3.verify import library_closure
from homm3.verify import library_code as lc
from homm3.verify.library_closure import Closure, Gap
from homm3.verify.library_code import Contribution, Library, section
from homm3.verify.test_library_code import _coff, _symbol, BASE, CODE, DATA, DIR32, REL32


class GapTests(unittest.TestCase):
    def gaps(self, category, claimed=(), folded=None):
        sizes = {rva: 8 for rva in category}
        return [(g.rva, g.before, g.after) for g in
                library_closure.gaps_of(category, sizes, set(claimed), lambda rva: f'm{rva:x}.obj',
                                        folded)]

    def test_an_unclaimed_body_between_library_neighbours_is_a_gap(self):
        self.assertEqual(self.gaps({0x10: 'runtime', 0x20: 'target', 0x30: 'runtime'}),
                         [(0x20, 'm10.obj', 'm30.obj')])

    def test_funclets_and_other_gaps_are_looked_past(self):
        self.assertEqual(self.gaps({0x10: 'runtime', 0x18: 'eh-funclet', 0x20: 'target',
                                    0x28: 'target', 0x30: 'runtime'}),
                         [(0x20, 'm10.obj', 'm30.obj'), (0x28, 'm10.obj', 'm30.obj')])

    def test_a_claimed_or_project_neighbour_is_no_library_region(self):
        self.assertEqual(self.gaps({0x10: 'target', 0x20: 'target', 0x30: 'runtime'},
                                   claimed={0x10}), [])
        self.assertEqual(self.gaps({0x10: 'zlib', 0x20: 'target', 0x30: 'runtime'}), [])
        self.assertEqual(self.gaps({0x20: 'target', 0x30: 'runtime'}), [])

    def test_a_claimed_library_fold_bounds_a_gap_as_its_member(self):
        category = {0x10: 'runtime', 0x20: 'target', 0x30: 'target', 0x40: 'runtime'}
        self.assertEqual(self.gaps(category, claimed={0x30}), [])
        self.assertEqual(self.gaps(category, claimed={0x30}, folded={0x30: 'filelist.obj'}),
                         [(0x20, 'm10.obj', 'filelist.obj')])


class ParentGapTests(unittest.TestCase):
    def gaps(self, category, sizes, owned=()):
        gaps = [library_closure.Gap(rva, sizes[rva], 'a.obj', 'a.obj')
                for rva in sorted(sizes) if category[rva] == 'target']
        return [(g.rva, g.size) for g in
                library_closure.parent_gaps(gaps, category, sizes, list(owned))]

    def test_split_pieces_join_the_unowned_library_body_before_them(self):
        category = {0x10: 'runtime', 0x30: 'target', 0x38: 'target', 0x40: 'runtime'}
        sizes = {0x10: 0x20, 0x30: 8, 0x38: 8, 0x40: 8}
        self.assertEqual(self.gaps(category, sizes), [(0x10, 0x30)])

    def test_an_owned_or_distant_body_keeps_the_gap(self):
        category = {0x10: 'runtime', 0x30: 'target', 0x40: 'runtime'}
        self.assertEqual(self.gaps(category, {0x10: 0x20, 0x30: 8, 0x40: 8}, [(0x10, 0x30)]),
                         [(0x30, 8)])
        self.assertEqual(self.gaps(category, {0x10: 0x10, 0x30: 8, 0x40: 8}), [(0x30, 8)])


class SectionSymbolTests(unittest.TestCase):
    def name(self, symbols):
        obj = _coff([('.text', CODE, b'\xc3\xc3', [])], symbols)
        return library_closure.section_symbol(section('LIBCMT.LIB', 'asm.obj', obj, 1))

    def test_public_before_static_at_the_lowest_offset(self):
        self.assertEqual(self.name([_symbol('_inner', 1, 1, 2), _symbol('_$E7', 0, 1, 3)]), '_$E7')
        self.assertEqual(self.name([_symbol('_$E7', 0, 1, 3), _symbol('_entry', 0, 1, 2)]), '_entry')

    def test_section_and_compiler_temporaries_name_nothing(self):
        self.assertEqual(self.name([_symbol('.text', 0, 1, 3), _symbol('$S12', 0, 1, 3)]), '-')
        self.assertEqual(self.name([_symbol('$L991', 0, 1, 3)]), '$L991')


def _named_coff(sections, symbols):
    """`_coff` with long symbol names: `symbols` are (name, value, section,
    storage) and a name over eight bytes goes to the string table."""
    from homm3.compare.canonicalize import CoffObject
    strings = bytearray()
    records = []
    for name, value, number, storage in symbols:
        encoded = name.encode()
        if len(encoded) > 8:
            head = struct.pack('<II', 0, 4 + len(strings))
            strings += encoded + b'\0'
        else:
            head = encoded.ljust(8, b'\0')
        records.append(head + struct.pack('<IhHBB', value, number, 0, storage, 0))
    with unittest.mock.patch('homm3.verify.test_library_code.CoffObject', bytes):
        payload = bytearray(_coff(sections, records))
    payload[-4:] = struct.pack('<I', 4 + len(strings))
    return CoffObject(bytes(payload + strings))


class FakePE:
    image_base = BASE

    def __init__(self, memory):
        self.memory = memory

    def read(self, rva, size):
        chunk = bytes(self.memory[rva:rva + size])
        return chunk if len(chunk) == size else None


def closure(objects, memory, *, rows=(), vtables=None, names=(), sizes=None, claimed=()):
    """A Closure over `objects` ({member: CoffObject} of LIBCMT) and retail
    `memory`, without the image, model and archives the real one loads."""
    c = Closure.__new__(Closure)
    c.mask = lambda name: name
    c.pe, c.base = FakePE(memory), BASE
    c.lib = Library({'LIBCMT': objects}, {}, {})
    c.known, c.where = defaultdict(set), defaultdict(set)
    for rva, name in names:
        c.name(rva, name)
    c.vt = dict(vtables or {})
    c.vt_at = {rva: cls for cls, rva in c.vt.items()}
    c.slot_classes = defaultdict(set)
    c.rows = list(rows)
    c.placed = {(r.library, r.member, r.section) for r in c.rows if r.section}
    c.owned = sorted((r.rva, r.rva + r.size) for r in c.rows if r.kind == 'code')
    c.sizes, c.claimed = dict(sizes or {}), set(claimed)
    c._starts = sorted(c.sizes)
    c.by_size = defaultdict(list)
    for (library, member), obj in c.lib.objects.items():
        for number, sec in enumerate(obj.sections, start=1):
            if sec.characteristics & lc.MEM_EXECUTE and (library, member, number) not in c.placed:
                c.by_size[len(obj.section_bytes(sec))].append(c.lib.section(library, member, number))
    c.sizes_desc = sorted(c.by_size, reverse=True)
    return c


def memory(**at):
    image = bytearray(0x3000)
    for rva, raw in at.items():
        offset = int(rva[1:], 16)
        image[offset:offset + len(raw)] = raw
    return image


def exact(rows, bad=()):
    """A library_code.verify stand-in: every row exact except `bad` keys."""
    return {Closure.key(r): SimpleNamespace(verdict='differs' if Closure.key(r) in bad else 'exact',
                                            reasons=['bytes differ'])
            for r in rows}


def code_row(rva, size, member='a.obj', number=1, symbol='_f', kind='code'):
    return Contribution(rva, size, 'LIBCMT', member, number, symbol, '-', kind)


ZERO = bytes.fromhex('33c0c3')                     # xor eax, eax; ret


class RefusalTests(unittest.TestCase):
    def one(self, raw, relocs, symbols, extra_sections=()):
        return {'a.obj': _coff([('.text', CODE, raw, relocs), *extra_sections], symbols)}

    def test_a_public_name_the_image_puts_elsewhere(self):
        objects = self.one(ZERO, [], [_symbol('_f', 0, 1, 2)])
        c = closure(objects, memory(x1000=ZERO), names=[(0x1800, '_f')])
        self.assertEqual(c.refusal(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000),
                         '_f is at 0x401800')
        c = closure(objects, memory(x1000=ZERO), names=[(0x1000, '_g')])
        self.assertEqual(c.refusal(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000),
                         '0x401000 is _g')
        c = closure(objects, memory(x1000=ZERO), names=[(0x1000, '_f')])
        self.assertIsNone(c.refusal(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000))

    def test_a_stored_vtable_must_be_the_rtti_one(self):
        # mov eax, OFFSET ??_7Foo@@6B@; ret
        objects = {'a.obj': _named_coff([('.text', CODE, bytes.fromhex('b800000000c3'),
                                          [(1, 1, DIR32)])],
                                        [('_f', 0, 1, 2), ('??_7Foo@@6B@', 0, 0, 2)])}
        retail = memory(x1000=b'\xb8' + struct.pack('<I', BASE + 0x2100) + b'\xc3')
        sec = lambda c: c.lib.section('LIBCMT', 'a.obj', 1)
        c = closure(objects, retail, vtables={'Foo': 0x2200})
        self.assertEqual(c.refusal(sec(c), 0x1000),
                         '??_7Foo@@6B@ is not the RTTI vtable at 0x402100')
        c = closure(objects, retail, vtables={'Foo': 0x2100})
        self.assertIsNone(c.refusal(sec(c), 0x1000))
        # any other symbol at an RTTI vtable's address is refused
        objects = self.one(bytes.fromhex('b800000000c3'), [(1, 1, DIR32)],
                           [_symbol('_f', 0, 1, 2), _symbol('_table', 0, 0, 2)])
        c = closure(objects, retail, vtables={'Bar': 0x2100})
        self.assertEqual(c.refusal(sec(c), 0x1000), '_table at the RTTI vtable of Bar')

    def test_a_referenced_datum_must_hold_its_bytes(self):
        # push OFFSET datum; ret
        objects = self.one(bytes.fromhex('6800000000c3'), [(1, 1, DIR32)],
                           [_symbol('_f', 0, 1, 2), _symbol('_d', 0, 2, 2)],
                           [('.data', DATA, b'LIB\0', [])])
        code = b'\x68' + struct.pack('<I', BASE + 0x2000) + b'\xc3'
        c = closure(objects, memory(x1000=code, x2000=b'XYZ\0'))
        self.assertEqual(c.refusal(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000),
                         '_d: datum bytes at 0x402000 differ')
        c = closure(objects, memory(x1000=code, x2000=b'LIB\0'))
        self.assertIsNone(c.refusal(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000))

    def test_a_deleting_destructor_needs_its_class_vtable(self):
        objects = self.one(ZERO, [], [_symbol('_f', 0, 1, 2)])
        for vtables, expected in (({}, 'deleting destructor of a class without an RTTI vtable'),
                                  ({'Foo': 0x2100}, None)):
            c = closure(objects, memory(x1000=ZERO), vtables=vtables)
            sec = dataclasses.replace(c.lib.section('LIBCMT', 'a.obj', 1),
                                      comdat='??_GFoo@@UAEPAXI@Z')
            self.assertEqual(c.refusal(sec, 0x1000), expected)

    def test_a_member_local_callee_must_match_where_it_is_reached(self):
        # call helper; ret, with the static helper in its own section
        objects = self.one(bytes.fromhex('e800000000c3'), [(1, 1, REL32)],
                           [_symbol('_f', 0, 1, 2), _symbol('_helper', 0, 2, 3)],
                           [('.text', CODE, ZERO, [])])
        call = b'\xe8' + struct.pack('<i', 0x1100 - 0x1005) + b'\xc3'
        c = closure(objects, memory(x1000=call, x1100=b'\x90\x90\xc3'))
        self.assertEqual(c.refusal(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000),
                         '_helper: its section does not match at 0x401100')
        c = closure(objects, memory(x1000=call, x1100=ZERO))
        self.assertIsNone(c.refusal(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000))


class OpenPositionTests(unittest.TestCase):
    def position(self, raw, end):
        c = closure({}, memory(x1000=raw), rows=[code_row(0x1000, 3)])
        return c.open_position(Gap(0x1000, end - 0x1000, 'a.obj', 'a.obj'), [])

    def test_fill_to_an_aligned_start_or_the_gap_end_is_skipped(self):
        self.assertEqual(self.position(ZERO + b'\xcc' + ZERO, 0x1010), 0x1004)
        self.assertIsNone(self.position(ZERO + b'\xcc', 0x1004))

    def test_unaligned_or_long_fill_is_open(self):
        self.assertEqual(self.position(ZERO + b'\xcc' * 3 + ZERO, 0x1010), 0x1003)
        self.assertEqual(self.position(ZERO + b'\xcc' * 17 + ZERO, 0x1020), 0x1003)

    def test_admitted_rows_cover_their_bytes(self):
        c = closure({}, memory(x1000=ZERO * 2))
        gap = Gap(0x1000, 6, 'a.obj', 'a.obj')
        self.assertEqual(c.open_position(gap, [(code_row(0x1000, 3), None)]), 0x1003)
        self.assertIsNone(c.open_position(gap, [(code_row(0x1000, 3), None),
                                                (code_row(0x1003, 3), None)]))


class CompleteTests(unittest.TestCase):
    def setUp(self):
        self.objects = {'a.obj': _coff([('.text', CODE, ZERO, []), ('.text', CODE, ZERO, [])],
                                       [_symbol('_f', 0, 1, 2), _symbol('_g', 0, 2, 2)])}

    def complete(self, admitted, rows=(), bad=()):
        c = closure(self.objects, memory(x1000=ZERO * 4), rows=rows)
        c.verify = lambda extra: exact(extra, bad)
        kept = c._complete([g for _r, _s, g in admitted], admitted)
        return [r.rva for r, _s, _g in kept]

    def admitted(self, rva, number, gap):
        sec = closure(self.objects, b'').lib.section('LIBCMT', 'a.obj', number)
        return (code_row(rva, 3, number=number), sec, gap)

    def test_a_gap_keeps_rows_only_when_they_cover_it(self):
        whole, part = Gap(0x1000, 3, 'a.obj', 'a.obj'), Gap(0x1003, 6, 'a.obj', 'a.obj')
        self.assertEqual(self.complete([self.admitted(0x1000, 1, whole),
                                        self.admitted(0x1003, 2, part)]), [0x1000])
        self.assertEqual(whole.rows[0][0].rva, 0x1000)
        self.assertEqual(part.rows, [])

    def test_a_public_name_keeps_one_address(self):
        gap = Gap(0x1000, 3, 'a.obj', 'a.obj')
        elsewhere = code_row(0x1800, 3, number=1)
        self.assertEqual(self.complete([self.admitted(0x1000, 1, gap)], rows=[elsewhere]), [])

    def test_a_row_the_final_proof_refutes_drops_its_gap(self):
        gap = Gap(0x1000, 3, 'a.obj', 'a.obj')
        self.assertEqual(self.complete([self.admitted(0x1000, 1, gap)],
                                       bad={(0x1000, 'LIBCMT', 'a.obj', 1, 'code')}), [])


class CompanionTests(unittest.TestCase):
    def test_a_local_callee_where_its_bytes_are_comes_along(self):
        objects = {'a.obj': _coff([('.text', CODE, bytes.fromhex('e800000000c3'), [(1, 1, REL32)]),
                                   ('.text', CODE, ZERO, [])],
                                  [_symbol('_f', 0, 1, 2), _symbol('_helper', 0, 2, 3)])}
        call = b'\xe8' + struct.pack('<i', 0x1100 - 0x1005) + b'\xc3'
        retail = memory(x1000=call, x1100=ZERO)
        for claimed, kind in (((), 'code'), ({0x1100}, 'alias')):
            with self.subTest(kind=kind):
                c = closure(objects, retail, sizes={0x1100: 3}, claimed=claimed)
                rows = c.companions(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000, set())
                self.assertEqual([(r.rva, r.section, r.symbol, r.kind) for r, _s in rows],
                                 [(0x1100, 2, '_helper', kind)])
        c = closure(objects, memory(x1000=call, x1100=b'\x90\x90\xc3'))
        self.assertEqual(c.companions(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000, set()), [])

    def test_an_external_callee_comes_along_only_where_the_image_names_it(self):
        objects = {'a.obj': _coff([('.text', CODE, bytes.fromhex('e800000000c3'), [(1, 1, REL32)])],
                                  [_symbol('_f', 0, 1, 2), _symbol('_ext', 0, 0, 2)]),
                   'b.obj': _coff([('.text', CODE, ZERO, [])], [_symbol('_ext', 0, 1, 2)])}
        call = b'\xe8' + struct.pack('<i', 0x1100 - 0x1005) + b'\xc3'
        retail = memory(x1000=call, x1100=ZERO)
        c = closure(objects, retail, names=[(0x1100, '_ext')])
        rows = c.companions(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000, set())
        self.assertEqual([(r.rva, r.member, r.symbol, r.kind) for r, _s in rows],
                         [(0x1100, 'b.obj', '_ext', 'code')])
        c = closure(objects, retail)
        self.assertEqual(c.companions(c.lib.section('LIBCMT', 'a.obj', 1), 0x1000, set()), [])


class UnattributedBytesTests(unittest.TestCase):
    """A relocation-free body is placed by a neighbour or a name, not bytes alone."""

    def why(self, member, names=(), rows=()):
        objects = {member: _coff([('.text', CODE, ZERO, [])],
                                 [_symbol('_zero', 0, 1, 2), _symbol('_ret', 2, 1, 2)])}
        c = closure(objects, memory(x1000=ZERO), names=names, rows=rows)
        sec = c.lib.section('LIBCMT', member, 1)
        return c.unattributed(sec, 0x1000, Gap(0x0f00, 0x200, 'a.obj', 'b.obj'), [])

    def test_an_unrelated_members_bytes_are_refused(self):
        self.assertIn('relocation-free', self.why('x.obj'))

    def test_a_neighbouring_bordering_or_named_member_is_attributed(self):
        self.assertIsNone(self.why('a.obj'))
        self.assertIsNone(self.why('b.obj'))
        self.assertIsNone(self.why('x.obj', names=[(0x1000, '_zero')]))
        # strchr.obj: the image names `_strchr` inside the section, not its start
        self.assertIsNone(self.why('x.obj', names=[(0x1002, '_ret')]))
        self.assertIn('relocation-free', self.why('x.obj', names=[(0x1001, '_ret')]))
        self.assertIsNone(self.why('x.obj', rows=[code_row(0x0ff0, 0x10, member='x.obj')]))
        self.assertIsNone(self.why('x.obj', rows=[code_row(0x1004, 0x10, member='x.obj')]))
        self.assertIn('relocation-free',
                      self.why('x.obj', rows=[code_row(0x0f00, 0x10, member='x.obj')]))


class FinalRowsTests(unittest.TestCase):
    """main writes the fold's adjacent member only where it proves in place."""

    def run_final(self, bad=()):
        objects = {m: _coff([('.text', CODE, ZERO, [])], [_symbol(f'_{m[0]}', 0, 1, 2)])
                   for m in ('a.obj', 'x.obj')}
        c = closure(objects, memory(x1000=ZERO))
        admitted = code_row(0x1000, 3, member='x.obj', symbol='_x')
        near = code_row(0x1000, 3, member='a.obj', symbol='_a')
        gap = Gap(0x1000, 3, 'a.obj', 'b.obj', rows=[(admitted, None)])
        c.verify = lambda rows: exact(rows, bad)
        return library_closure.final_rows(c, [gap], {Closure.key(admitted): [near]})

    def test_the_adjacent_members_copy_replaces_a_fold(self):
        (row, why), = self.run_final()
        self.assertEqual(row.member, 'a.obj')
        self.assertIn('identical code', why)

    def test_a_substitute_that_fails_in_place_reverts(self):
        (row, _why), = self.run_final(bad={(0x1000, 'LIBCMT', 'a.obj', 1, 'code')})
        self.assertEqual(row.member, 'x.obj')

    def test_a_row_that_fails_as_written_is_not_written(self):
        self.assertEqual(self.run_final(bad={(0x1000, 'LIBCMT', 'a.obj', 1, 'code'),
                                             (0x1000, 'LIBCMT', 'x.obj', 1, 'code')}), [])


if __name__ == '__main__':
    unittest.main()
