"""A candidate's local-static guard takes the target's DATA_COMPGEN_GUARD name,
or the masked `$S` spelling another image's placements give it."""
import struct
import unittest

from homm3.build import canonicalize_data_symbols as canon
from homm3.build.normalize_objs import _canonicalize_claimed_guards, _canonicalize_masked_guards
from homm3.build.test_icf_alias_normalization import CALL, coff

SCOPE = "@?1??load@@YI_NXZ"
GUARD = f"_?$S28{SCOPE}@4EA"
OWNER = f"_?strings{SCOPE}@4V?$TAutoArrayPtr@D@@A"
OTHER = f"_?names{SCOPE}@4V?$TAutoArrayPtr@D@@A"
CLAIM = "__h3cg$unit$static_init_guard$stringsGuard"
OWNERS = {CLAIM: ("strings", 0x2938d4)}


def names(payload: bytes) -> set[str]:
    return {symbol.name for symbol in canon.CoffObject(payload).symbols.values()}


class ClaimedGuardTest(unittest.TestCase):
    def test_guard_in_the_owner_scope_takes_the_claim_name(self):
        base = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, GUARD)], [GUARD, OWNER])
        target = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, CLAIM)], [CLAIM])
        renamed, count = _canonicalize_claimed_guards(base, target, "unit", OWNERS)
        self.assertEqual(count, 1)
        self.assertIn(CLAIM, names(renamed))
        self.assertNotIn(GUARD, names(renamed))

    def test_a_target_spelling_the_guard_stays_unchanged(self):
        base = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, GUARD)], [GUARD, OWNER])
        target = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, GUARD)], [GUARD])
        self.assertEqual(_canonicalize_claimed_guards(base, target, "unit", OWNERS),
                         (base, 0))

    def test_another_units_claim_or_an_ambiguous_scope_stays_visible(self):
        base = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, GUARD)], [GUARD, OWNER])
        target = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, CLAIM)], [CLAIM])
        self.assertEqual(_canonicalize_claimed_guards(base, target, "other", OWNERS),
                         (base, 0))
        second = f"_?$S29{SCOPE}@4EA"
        base = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, GUARD)],
                    [GUARD, second, OWNER, OTHER])
        self.assertEqual(_canonicalize_claimed_guards(base, target, "unit", OWNERS),
                         (base, 0))


INLINE_FUNCTION = "?getNames@?A0x1234@@YAABVNames@1@XZ"
INLINE_GUARD = "??_B?1??getNames@?A0x1234@@YAABVNames@1@XZ@51"
INLINE_OWNER = "?strings@?1??getNames@?A0x1234@@YAABVNames@2@XZ@4V32@B"
INLINE_CTOR = "??0Names@?A0x1234@@QAE@XZ"
INLINE_DTOR = "?strings@?1??getNames@?A0x1234@@YAABVNames@1@XZ@$AV21@B"
INLINE_BODY = (b"\xf6\x05\0\0\0\0\x01\x75\x11"
               b"\x80\x0d\0\0\0\0\x01\xb9\0\0\0\0"
               b"\xe8\0\0\0\0\xb8\0\0\0\0\xc3")
INLINE_CLEANUP_BODY = (b"\xf6\x05\0\0\0\0\x01\x75\x1c"
                       b"\x80\x0d\0\0\0\0\x01\xb9\0\0\0\0"
                       b"\xe8\0\0\0\0\x68\0\0\0\0"
                       b"\xe8\0\0\0\0\x59\xb8\0\0\0\0\xc3")


def inline_guard_object(guard, *, second_guard=None, owner=INLINE_OWNER,
                        ctor=INLINE_CTOR, body=INLINE_BODY, extra=(), cleanup=False):
    second_guard = guard if second_guard is None else second_guard
    refs = [(2, guard), (11, second_guard), (17, owner), (22, ctor), (27, owner)]
    data_indices = (0, 1, 2, 4)
    functions = {ctor}
    if cleanup:
        body = INLINE_CLEANUP_BODY
        refs = refs[:4] + [(27, INLINE_DTOR), (32, "_atexit"), (38, owner)]
        data_indices = (0, 1, 2, 4, 6)
        functions.update((INLINE_DTOR, "_atexit"))
    externs = list(dict.fromkeys([name for _, name in refs] + list(extra)))
    payload = bytearray(coff(body, [(INLINE_FUNCTION, 0)], refs, externs))
    relocation_offset = struct.unpack_from("<I", payload, 20 + 24)[0]
    for index in data_indices:
        struct.pack_into("<H", payload, relocation_offset + index * 10 + 8, 6)
    for symbol in canon.CoffObject(payload).symbols.values():
        if symbol.name in externs and symbol.name not in functions:
            struct.pack_into("<H", payload, symbol.offset + 14, 0)
    return bytes(payload)


class InlineClaimedGuardTest(unittest.TestCase):
    def test_the_statics_cleanup_name_is_not_a_second_data_owner(self):
        base = inline_guard_object(INLINE_GUARD, cleanup=True)
        target = inline_guard_object(CLAIM, cleanup=True)
        renamed, count = _canonicalize_claimed_guards(base, target, "unit", OWNERS)
        self.assertEqual(count, 1)
        self.assertIn(CLAIM, names(renamed))
        self.assertIn(INLINE_DTOR, names(renamed))

    def test_complete_initializer_proves_guard_with_different_backreferences(self):
        # Inline COMDAT guards do not use $S, and their owner's enclosing
        # signature has different name backreferences. Both owner spellings
        # occur in VC6 objects; do not erase the function's type/signature.
        for owner in (INLINE_OWNER, "_" + INLINE_OWNER):
            with self.subTest(owner=owner):
                base = inline_guard_object(INLINE_GUARD, owner=owner)
                target = inline_guard_object(CLAIM, owner=owner)
                renamed, count = _canonicalize_claimed_guards(base, target, "unit", OWNERS)
                self.assertEqual(count, 1)
                self.assertIn(CLAIM, names(renamed))
                self.assertNotIn(INLINE_GUARD, names(renamed))
                self.assertEqual(_canonicalize_claimed_guards(renamed, target, "unit", OWNERS),
                                 (renamed, 0))

    def test_a_different_initializer_callee_is_not_masked(self):
        base = inline_guard_object(INLINE_GUARD)
        target = inline_guard_object(CLAIM, ctor="??0Other@@QAE@XZ")
        self.assertEqual(_canonicalize_claimed_guards(base, target, "unit", OWNERS), (base, 0))

    def test_the_claimed_owner_must_be_referenced_in_the_matching_body(self):
        other = INLINE_OWNER.replace("?strings@", "?other@", 1)
        for cleanup in (False, True):
            base = inline_guard_object(INLINE_GUARD, owner=other, extra=(INLINE_OWNER,),
                                       cleanup=cleanup)
            target = inline_guard_object(CLAIM, owner=other, cleanup=cleanup)
            self.assertEqual(_canonicalize_claimed_guards(base, target, "unit", OWNERS),
                             (base, 0))

    def test_a_different_named_function_is_not_an_identity_anchor(self):
        original = inline_guard_object(INLINE_GUARD)
        obj = canon.CoffObject(original)
        index = next(s.index for s in obj.symbols.values() if s.name == INLINE_FUNCTION)
        base = canon._rewrite_names(obj, {index: "?different@@YAXXZ"})
        target = inline_guard_object(CLAIM)
        self.assertEqual(_canonicalize_claimed_guards(base, target, "unit", OWNERS), (base, 0))

    def test_guard_relocation_types_must_agree(self):
        payload = bytearray(inline_guard_object(INLINE_GUARD))
        relocation_offset = struct.unpack_from("<I", payload, 20 + 24)[0]
        struct.pack_into("<H", payload, relocation_offset + 8, 0x14)
        base, target = bytes(payload), inline_guard_object(CLAIM)
        self.assertEqual(_canonicalize_claimed_guards(base, target, "unit", OWNERS), (base, 0))

    def test_instruction_and_addend_changes_remain_visible(self):
        base = inline_guard_object(INLINE_GUARD)
        changed_bit = bytearray(INLINE_BODY)
        changed_bit[6] = 2
        changed_addend = bytearray(INLINE_BODY)
        struct.pack_into("<I", changed_addend, 17, 4)
        for body in (changed_bit, changed_addend):
            target = inline_guard_object(CLAIM, body=bytes(body))
            self.assertEqual(_canonicalize_claimed_guards(base, target, "unit", OWNERS),
                             (base, 0))

    def test_two_candidate_guards_cannot_be_merged_into_one_claim(self):
        second = INLINE_GUARD.replace("??_B?1", "??_B?2", 1)
        base = inline_guard_object(INLINE_GUARD, second_guard=second)
        target = inline_guard_object(CLAIM)
        self.assertEqual(_canonicalize_claimed_guards(base, target, "unit", OWNERS), (base, 0))


MASKED = f"_?$S{SCOPE}@4EA"


class MaskedGuardTest(unittest.TestCase):
    # the target defines the guard it names (the helper puts definitions in .text)
    def test_the_counter_spelling_takes_the_targets_masked_name(self):
        base = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, GUARD)], [GUARD, OWNER])
        target = coff(CALL, [("?load@@YI_NXZ", 0), (MASKED, 4)], [(1, MASKED)])
        renamed, count = _canonicalize_masked_guards(base, target)
        self.assertEqual(count, 1)
        self.assertIn(MASKED, names(renamed))
        self.assertNotIn(GUARD, names(renamed))

    def test_two_guards_of_one_spelling_stay_visible(self):
        second = f"_?$S29@?2??load@@YI_NXZ@4EA"
        base = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, GUARD)], [GUARD, second, OWNER])
        target = coff(CALL, [("?load@@YI_NXZ", 0), (MASKED, 4)], [(1, MASKED)])
        self.assertEqual(_canonicalize_masked_guards(base, target), (base, 0))

    def test_a_target_without_masked_guards_leaves_the_candidate(self):
        base = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, GUARD)], [GUARD, OWNER])
        target = coff(CALL, [("?load@@YI_NXZ", 0)], [(1, GUARD)], [GUARD])
        self.assertEqual(_canonicalize_masked_guards(base, target), (base, 0))


if __name__ == "__main__":
    unittest.main()
