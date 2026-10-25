"""Same-owner local-static teardowns bind by their registration order.

MapEditorText's INITIALIZE_TEXT defines one `apBuffer` per expansion, so
every claim's owner check sees every teardown. The claims carry their
definitions' source ordinals (`$k`), and the k-th binds to the `$E` thunk
the function registers k-th, whatever the thunks' own emission order.
"""
from types import SimpleNamespace
import unittest
import warnings

from homm3.build.canonicalize_data_symbols import (
    CompgenClaim, Symbol, Relocation, _compgen_renames,
    DIR32, FUNCTION_TYPE, MEM_EXECUTE)
from homm3.retail_labels.source import _number_repeated_static_dtors

REL32 = 0x14
SCOPES = ("BNE", "BMO", "BMI")


def fixture(registered=(0, 1, 2), extra_dtor=False, slot_store=False, second_parent=(),
            selection=None):
    """One parent registering three thunks in `registered` order; thunk i
    tears down `apBuffer` at scope SCOPES[i]. Thunks in `second_parent`
    are registered by another function instead; with `selection`, both
    parents are /Gy COMDATs of that selection."""
    symbols = {
        1: Symbol(1, 0, "?initialize@SText@@YAXXZ", 0, 1, FUNCTION_TYPE, 2, 0),
        2: Symbol(2, 0, "_atexit", 0, 0, 0, 2, 0),
        3: Symbol(3, 0, "??3@YAXPAX@Z", 0, 0, 0, 2, 0),
    }
    bodies = [b""]
    refs = []
    thunks = list(range(len(SCOPES) + (1 if extra_dtor else 0)))
    for i in thunks:
        thunk, datum = 10 + i, 20 + i
        scope = SCOPES[i] if i < len(SCOPES) else "BLM"
        symbols[thunk] = Symbol(thunk, 0, f"_$E{100 + i}", 0, 2 + i, FUNCTION_TYPE, 3, 0)
        symbols[datum] = Symbol(datum, 0, f"_?apBuffer@?{scope}@??initialize@SText@@YAXXZ"
                                "@4V?$TAutoArrayPtr@D@@A", 0, 0, 0, 3, 0)
        # mov ecx, datum; call operator delete; ret
        bodies.append(b"\xb9\0\0\0\0\xe8\0\0\0\0\xc3")
        refs += [Relocation(2 + i, 1, datum, DIR32), Relocation(2 + i, 6, 3, REL32)]
    parents = {1: b"", len(bodies) + 1: b""}
    if second_parent:
        symbols[4] = Symbol(4, 0, "?other@SText@@YAXXZ", 0, len(bodies) + 1,
                            FUNCTION_TYPE, 2, 0)
        bodies.append(b"")
    for i in registered:
        # push OFFSET thunk (or mov [esp], OFFSET thunk); call _atexit
        section = len(bodies) if i in second_parent else 1
        parent = parents[section]
        prefix = b"\xc7\x04\x24" if slot_store else b"\x68"
        refs += [Relocation(section, len(parent) + len(prefix), 10 + i, DIR32),
                 Relocation(section, len(parent) + len(prefix) + 5, 2, REL32)]
        parents[section] = parent + prefix + b"\0\0\0\0\xe8\0\0\0\0\x59"
    bodies[0] = parents[1] + b"\xc3"
    if second_parent:
        bodies[-1] = parents[len(bodies)] + b"\xc3"
    sections = [SimpleNamespace(index=i + 1, raw_size=len(body), characteristics=MEM_EXECUTE,
                                name=".text")
                for i, body in enumerate(bodies)]
    data = b""
    if selection is not None:
        # each parent's section symbol and its aux record (selection at +14)
        for number in (1, len(bodies)):
            sections[number - 1].characteristics |= 0x1000
            symbols[50 + number] = Symbol(50 + number, len(data), ".text", 0, number, 0, 3, 1)
            data += bytes(18) + bytes(14) + bytes([selection]) + bytes(3)
    return SimpleNamespace(symbols=symbols, sections=sections, relocations=refs, data=data,
                           section_bytes=lambda section: bodies[section.index - 1])


def claims(count=3):
    return tuple(CompgenClaim(f"__h3cg$MapEditorText$static_dtor$apBuffer${k}",
                              "STATIC_DTOR", "apBuffer", 11)
                 for k in range(1, count + 1))


class RepeatedStaticDestructorTest(unittest.TestCase):
    def bind(self, coff, group=None):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return _compgen_renames(coff, group or claims())[0]

    def test_ordinals_follow_registration_not_emission(self):
        names = self.bind(fixture(registered=(2, 0, 1)))
        self.assertEqual(names, {
            12: "__h3cg$MapEditorText$static_dtor$apBuffer$1",
            10: "__h3cg$MapEditorText$static_dtor$apBuffer$2",
            11: "__h3cg$MapEditorText$static_dtor$apBuffer$3",
        })

    def test_argument_slot_store_is_a_registration(self):
        names = self.bind(fixture(registered=(1, 2, 0), slot_store=True))
        self.assertEqual(names[11], "__h3cg$MapEditorText$static_dtor$apBuffer$1")

    def test_claim_order_does_not_matter(self):
        names = self.bind(fixture(), tuple(reversed(claims())))
        self.assertEqual(names[10], "__h3cg$MapEditorText$static_dtor$apBuffer$1")

    def test_count_mismatch_binds_nothing(self):
        self.assertEqual(self.bind(fixture(extra_dtor=True)), {})
        self.assertEqual(self.bind(fixture(), claims(2)), {})

    def test_unregistered_teardown_binds_nothing(self):
        self.assertEqual(self.bind(fixture(registered=(0, 1))), {})

    def test_two_ordinary_parents_bind_in_their_source_order(self):
        # GameMap's importText and exportText each define kSectionStart:
        # non-inline definitions are emitted, and numbered, in source order.
        for selection in (None, 1):
            with self.subTest(selection=selection):
                names = self.bind(fixture(registered=(1, 2, 0), second_parent=(0,),
                                          selection=selection))
                self.assertEqual(names, {
                    11: "__h3cg$MapEditorText$static_dtor$apBuffer$1",
                    12: "__h3cg$MapEditorText$static_dtor$apBuffer$2",
                    10: "__h3cg$MapEditorText$static_dtor$apBuffer$3",
                })

    def test_an_inline_parent_among_several_binds_nothing(self):
        # Ordinals count definitions file-wide, but an inline or template
        # parent (SELECT_ANY) is emitted where a user needs it, so section
        # order no longer follows its definitions.
        self.assertEqual(self.bind(fixture(second_parent=(0,), selection=2)), {})
        self.assertEqual(self.bind(fixture(selection=2))[10],
                         "__h3cg$MapEditorText$static_dtor$apBuffer$1")

    def test_source_ordinals_only_for_repeated_owners(self):
        rows = [{"ckind": "STATIC_DTOR", "owner": "apBuffer", "name": "a"},
                {"ckind": "STATIC_DTOR", "owner": "slots", "name": "s"},
                {"ckind": "STATIC_DTOR", "owner": "apBuffer", "name": "a"},
                {"ckind": "STATIC_CTOR", "owner": "apBuffer", "name": "c"}]
        _number_repeated_static_dtors(rows)
        self.assertEqual([row["name"] for row in rows], ["a$1", "s", "a$2", "c"])


if __name__ == "__main__":
    unittest.main()
