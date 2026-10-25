"""A VA on BEGIN_MESSAGE_MAP names the class's GetMessageMap, anonymous or not."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from homm3.retail_labels import source


class MessageMapKeysTest(unittest.TestCase):
    def test_message_maps_bind_by_their_class(self):
        declarations = (
            "BEGIN_MESSAGE_MAP(TNamedDlg, CDialog)",
            "BEGIN_MESSAGE_MAP( TAnonymousDlg , CDialog )",
        )
        symbols = (
            "?GetMessageMap@TNamedDlg@@MBEPBUAFX_MSGMAP@@XZ",
            "?GetMessageMap@TAnonymousDlg@?%C:\\Dev\\Editor\\Dlg.cpp1234@@"
            "MBEPBUAFX_MSGMAP@@XZ",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dialogs.cpp"
            path.write_text("\n".join(
                f"VA(0x{0x401000 + i * 0x20:08x}, 0x6)\n{decl}\nEND_MESSAGE_MAP()"
                for i, decl in enumerate(declarations)))
            rows = source.scan_file(path, {0x1000, 0x1020})
        self.assertEqual([row["name"] for row in rows],
                         ["TNamedDlg_GetMessageMap", "TAnonymousDlg_GetMessageMap"])
        groups = {source._demangle_key(symbol): [(symbol, 0x6)]
                  for symbol in symbols}
        with mock.patch.object(source, "_base_authority_scan",
                               return_value=(groups, {})):
            source.join_unit("dialogs", rows)
        self.assertEqual(rows[0].get("joined"), symbols[0])

    def test_ir_names_an_anonymous_class_message_map(self):
        ir = "\n".join((
            'define internal x86_thiscallcc noundef ptr '
            '@"?GetMessageMap@TAnonymousDlg@?A0xE345B8E0@@MBEPBUAFX_MSGMAP@@XZ"(ptr %0) {',
            'define dso_local x86_thiscallcc noundef ptr '
            '@"?GetMessageMap@TNamedDlg@@MBEPBUAFX_MSGMAP@@XZ"(ptr %0) {',
        ))
        text = ("VA(0x00480112, 0x6)\nBEGIN_MESSAGE_MAP(TAnonymousDlg, CDialog)\n"
                "END_MESSAGE_MAP()\n\nVA(0x004801ff, 0x6)  // note\n"
                "BEGIN_MESSAGE_MAP(TMissingDlg, CDialog)\nEND_MESSAGE_MAP()\n")
        self.assertEqual(source.ir_message_map_names(ir, text), {
            0x80112: "?GetMessageMap@TAnonymousDlg@?A0xE345B8E0@@MBEPBUAFX_MSGMAP@@XZ"})


class EmitAllDeclsTest(unittest.TestCase):
    """A message map reached only from another class's handler is unused, so
    Clang emits none of its members unless asked for every declaration."""
    TEXT = ("VA(0x00409cc3, 0x6)\nBEGIN_MESSAGE_MAP(TSelectSpellDlg, CDialog)\n"
            "END_MESSAGE_MAP()\n")
    MAP = ('define dso_local x86_thiscallcc noundef ptr '
           '@"?GetMessageMap@TSelectSpellDlg@@MBEPBUAFX_MSGMAP@@XZ"(ptr %0) {')

    def names(self, first, every):
        calls = []

        def emit(path, extra=None, *, profiles=None):
            calls.append(extra)
            return first if extra is None else every
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SpellsDlg.cpp"
            path.write_text(self.TEXT)
            with mock.patch.object(source.clang, "emit_ir", side_effect=emit), \
                    mock.patch.object(source, "shared_image_source", return_value=False):
                return source.unit_ir_names(path), calls

    def test_an_unbound_map_is_read_from_every_declaration(self):
        names, calls = self.names("", self.MAP)
        self.assertEqual(names, {0x9cc3: "?GetMessageMap@TSelectSpellDlg@@MBEPBUAFX_MSGMAP@@XZ"})
        self.assertEqual(calls, [None, ["-Xclang", "-femit-all-decls"]])

    def test_a_bound_map_needs_no_second_emission(self):
        names, calls = self.names(self.MAP, None)
        self.assertEqual(set(names), {0x9cc3})
        self.assertEqual(calls, [None])

    def test_an_unreadable_tu_or_second_emission(self):
        self.assertEqual(self.names(None, self.MAP), (None, [None]))
        names, calls = self.names("", None)
        self.assertEqual((names, len(calls)), ({}, 2))


if __name__ == "__main__":
    unittest.main()
