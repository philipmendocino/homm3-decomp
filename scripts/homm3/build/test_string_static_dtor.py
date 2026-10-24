"""A local static string's teardown is its inlined ~basic_string: `_Tidy(true)`.

GameMap's importText registers one `$E` per section string (h3maped
0x425b8c, 0x425b99), each `push 1; mov ecx, string; call _Tidy; ret`. One
parent registers both callbacks, so its registration proves neither, and the
thunk reaches no destructor or operator delete. A string constructor reaches
`_Tidy` too, beside `assign`, and must stay no candidate.
"""
from types import SimpleNamespace
import unittest
import warnings

from homm3.build.canonicalize_data_symbols import (
    CompgenClaim, Symbol, Relocation, _compgen_renames,
    DIR32, FUNCTION_TYPE, MEM_EXECUTE)

REL32 = 0x14
TIDY = "?_Tidy@?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@AAEX_N@Z"
ASSIGN = "?assign@?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@QAEAAV12@PBDI@Z"
OWNERS = ("kSectionStart", "kSectionEnd")


def fixture(constructor=False):
    symbols = {
        1: Symbol(1, 0, "?importText@@YAXXZ", 0, 1, FUNCTION_TYPE, 2, 0),
        2: Symbol(2, 0, "_atexit", 0, 0, 0, 2, 0),
        3: Symbol(3, 0, TIDY, 0, 0, 0, 2, 0),
        4: Symbol(4, 0, ASSIGN, 0, 0, 0, 2, 0),
    }
    bodies, refs, parent = [b""], [], b""
    for i, owner in enumerate(OWNERS):
        thunk, datum = 10 + i, 20 + i
        symbols[thunk] = Symbol(thunk, 0, f"_$E{100 + i}", 0, 2 + i, FUNCTION_TYPE, 3, 0)
        symbols[datum] = Symbol(datum, 0, f"_?{owner}@?1??importText@@YAXXZ@4V?$basic_string@"
                                "DU?$char_traits@D@std@@V?$allocator@D@2@@std@@B", 0, 0, 0, 3, 0)
        # push 1; mov ecx, datum; call _Tidy; ret
        bodies.append(b"\x6a\x01\xb9\0\0\0\0\xe8\0\0\0\0\xc3")
        refs += [Relocation(2 + i, 3, datum, DIR32), Relocation(2 + i, 8, 3, REL32)]
        # push OFFSET thunk; call _atexit; pop ecx
        refs += [Relocation(1, len(parent) + 1, thunk, DIR32),
                 Relocation(1, len(parent) + 6, 2, REL32)]
        parent += b"\x68\0\0\0\0\xe8\0\0\0\0\x59"
    if constructor:
        # mov ecx, datum; call _Tidy; push ...; call assign; ret
        symbols[30] = Symbol(30, 0, "_$E200", 0, 4, FUNCTION_TYPE, 3, 0)
        bodies.append(b"\xb9\0\0\0\0\xe8\0\0\0\0\xe8\0\0\0\0\xc3")
        refs += [Relocation(4, 1, 20, DIR32), Relocation(4, 6, 3, REL32),
                 Relocation(4, 11, 4, REL32)]
    bodies[0] = parent + b"\xc3"
    sections = [SimpleNamespace(index=i + 1, raw_size=len(body), characteristics=MEM_EXECUTE)
                for i, body in enumerate(bodies)]
    return SimpleNamespace(symbols=symbols, sections=sections, relocations=refs,
                           section_bytes=lambda section: bodies[section.index - 1])


class StringStaticDestructorTest(unittest.TestCase):
    def bind(self, coff):
        claims = tuple(CompgenClaim(f"__h3cg$GameMap$static_dtor${owner}",
                                    "STATIC_DTOR", owner, 13) for owner in OWNERS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return _compgen_renames(coff, claims)[0]

    def test_each_string_binds_its_tidy_thunk(self):
        self.assertEqual(self.bind(fixture()), {
            10: "__h3cg$GameMap$static_dtor$kSectionStart",
            11: "__h3cg$GameMap$static_dtor$kSectionEnd",
        })

    def test_a_string_constructor_reaching_tidy_is_no_teardown(self):
        self.assertEqual(self.bind(fixture(constructor=True))[10],
                         "__h3cg$GameMap$static_dtor$kSectionStart")


if __name__ == "__main__":
    unittest.main()
