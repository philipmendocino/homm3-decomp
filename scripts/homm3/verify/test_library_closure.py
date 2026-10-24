"""Census gaps between library neighbours and the names their rows take."""
import unittest

from homm3.verify import library_closure
from homm3.verify.library_code import section
from homm3.verify.test_library_code import _coff, _symbol, CODE


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


if __name__ == '__main__':
    unittest.main()
