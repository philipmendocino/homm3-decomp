"""placements._compgen_names: a shared unit's VA_COMPGEN_AT claims name its `$E`s.

resourcemanager's g_soundHeaderDescriptors initializer is a CRT body only the
map editor scores (the game's slot is the body itself). The game claims no
STATIC_CTOR for it, so only the editor's own VA_COMPGEN_AT can name the `$E`
whose DIR32 fields then place the descriptor table.
"""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from homm3.census import placements


class OwnCompgenNamesTests(unittest.TestCase):
    def names(self, owner_rows, own):
        seen = []

        def renames(coff, claims):
            seen.extend(claims)
            return {1: claims[0].name} if claims else {}, ()
        coff = SimpleNamespace(symbols={1: SimpleNamespace(name="_$E34")})
        with tempfile.TemporaryDirectory() as tmp:
            obj = Path(tmp) / "resourcemanager.obj"
            obj.write_bytes(b"")
            owner = Path(tmp) / "compgen_claims.tsv"
            if owner_rows is not None:
                owner.write_text("")
            with mock.patch.object(placements, "_owner_compgen", return_value=owner), \
                    mock.patch("homm3.compare.canonicalize.load_compgen_claims",
                               return_value=owner_rows or ()), \
                    mock.patch("homm3.compare.canonicalize.CoffObject", return_value=coff), \
                    mock.patch("homm3.compare.canonicalize._compgen_renames", renames):
                out = placements._compgen_names(obj, "resourcemanager",
                                                "src/resourcemanager.cpp", own)
        return out, [(c.name, c.kind, c.owner) for c in seen]

    def test_an_own_static_constructor_names_the_initializer(self):
        name = "__h3cg$resourcemanager$static_ctor$g_soundHeaderDescriptors"
        out, claims = self.names(None, [(name, "STATIC_CTOR", "g_soundHeaderDescriptors"),
                                        ("__h3cg$resourcemanager$implicit_dtor$basic_ostream",
                                         "IMPLICIT_DTOR", "basic_ostream")])
        self.assertEqual(out, {"_$E34": name})
        # a direct-symbol kind names a COFF symbol already; it is no `$E`
        self.assertEqual(claims, [(name, "STATIC_CTOR", "g_soundHeaderDescriptors")])

    def test_the_owners_claim_is_not_repeated(self):
        name = "__h3cg$artifact$static_dtor$artifactSlotStrings"
        row = SimpleNamespace(name=name, kind="STATIC_DTOR", owner="artifactSlotStrings")
        _out, claims = self.names([row], [(name, "STATIC_DTOR", "artifactSlotStrings")])
        self.assertEqual(claims, [(name, "STATIC_DTOR", "artifactSlotStrings")])


if __name__ == "__main__":
    unittest.main()
