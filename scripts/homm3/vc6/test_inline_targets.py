"""ICF-aware, site-aligned inline targets (`inline_targets`)."""
import os
import unittest
from unittest import mock

from homm3.vc6 import inline_model, inline_targets, inline_solve
from homm3.vc6.inline_solve import Edit, Model, Search
from homm3.vc6.inline_targets import Body, Equivalence, root_target


def body(code: bytes, *referents):
    """A body whose relocation fields follow `code`, one per referent."""
    payload = bytearray(code)
    sites = []
    for _ in referents:
        payload += b"\xe8"
        sites.append(len(payload))
        payload += b"\0\0\0\0"
    payload += b"\xc3"
    return Body(bytes(payload), tuple(sites),
                tuple((0x14, name, True) for name in referents))


# Our compile: two vector<T *>::insert instances with one body that call
# their own (identical) _Ufill; retail labels the folded body by the widget
# instance. push_back<int> and push_back<pos> share bytes but call
# different inserts, and retail keeps both at their own addresses.
OURS = {
    "insert<def*>": body(b"\x55\x8b\xec", "_Ufill<def*>"),
    "_Ufill<def*>": body(b"\x8b\x45\x08"),
    "push_back<int>": body(b"\x51", "insert<int>"),
    "push_back<pos>": body(b"\x51", "insert<pos>"),
    "insert<int>": body(b"\x90\x90"),
    "insert<pos>": body(b"\x6a\x0c\x90"),
    "size<def*>": body(b"\x8b\x41\x04"),
}
RETAIL = {
    "insert<widget*>": body(b"\x55\x8b\xec", "_Ufill<widget*>"),
    "_Ufill<widget*>": body(b"\x8b\x45\x08"),
    "erase<widget*>": body(b"\x55\x8b\xec", "_Destroy<widget*>"),
    "_Destroy<widget*>": body(b"\x33\xc0"),
}
ADDRESSES = {"push_back<int>": 0x1000, "push_back<pos>": 0x2000}


def equivalence(**extra):
    address = dict(ADDRESSES, **extra.pop("address", {}))
    return Equivalence(ours=OURS.get, retail=RETAIL.get, address=address.get, **extra)


class EquivalenceTests(unittest.TestCase):
    def test_an_icf_twin_is_the_same_callee(self):
        self.assertEqual(equivalence().reason("insert<def*>", "insert<widget*>"), "ICF twin")

    def test_identical_bytes_with_different_referents_differ(self):
        # erase<widget*> has insert's bytes, but its call reaches _Destroy.
        self.assertIsNone(equivalence().reason("insert<def*>", "erase<widget*>"))

    def test_a_different_body_differs(self):
        self.assertIsNone(equivalence().reason("size<def*>", "insert<widget*>"))

    def test_two_retail_addresses_are_two_functions(self):
        self.assertIsNone(equivalence().reason("push_back<int>", "push_back<pos>"))
        same = equivalence(address={"push_back<pos>": 0x1000})
        self.assertEqual(same.reason("push_back<int>", "push_back<pos>"), "retail address")

    def test_the_normalizer_twin_rule_is_kept(self):
        twins = equivalence(twins={("size<def*>", "size<widget*>")})
        self.assertEqual(twins.reason("size<def*>", "size<widget*>"), "normalizer twin")


class ImageTests(unittest.TestCase):
    """A working label with no delinked body is compared with retail's
    bytes at its address, call fields resolved to their targets."""
    RVA = 0x34400

    def image(self, target):
        code = bytearray(OURS["push_back<int>"].payload)
        site = OURS["push_back<int>"].sites[0]
        code[site:site + 4] = (target - (self.RVA + site + 4)).to_bytes(4, "little", signed=True)
        return {self.RVA: (bytes(code) + b"\xcc\xcc", 0x400000)}.get

    def test_the_bytes_at_the_label_s_address_with_the_same_call_target(self):
        address = dict(ADDRESSES, **{"fn_34400": self.RVA, "insert<int>": 0x5000})
        address.pop("push_back<int>")
        same = Equivalence(OURS.get, RETAIL.get, address.get, image=self.image(0x5000))
        self.assertEqual(same.reason("push_back<int>", "fn_34400"), "retail image bytes")
        other = Equivalence(OURS.get, RETAIL.get, address.get, image=self.image(0x6000))
        self.assertIsNone(other.reason("push_back<int>", "fn_34400"))


def calls(*names):
    return [(10 * i, name) for i, name in enumerate(names)]


class TargetTests(unittest.TestCase):
    def test_folded_labels_cancel_and_real_differences_stay(self):
        ours = calls("new", "insert<def*>", "new", "insert<def*>", "new", "insert<def*>")
        retail = calls("new", "insert<widget*>", "new", "insert<widget*>", "new")
        target = root_target("root", ours, retail, {"insert<def*>"}, equivalence())
        # By name every call would differ (-3/+2); per site one call differs.
        self.assertEqual(target.wanted, {"insert<def*>": -1})
        self.assertEqual(len(target.label_only), 2)
        self.assertEqual([(r.ours, r.retail) for r in target.rows], [("insert<def*>", None)])
        self.assertEqual(target.group["insert<widget*>"], "insert<def*>")

    def test_a_same_byte_body_with_other_callees_is_a_difference(self):
        target = root_target("root", calls("insert<def*>"), calls("erase<widget*>"),
                             {"insert<def*>"}, equivalence())
        self.assertEqual(target.wanted, {"insert<def*>": -1})

    def test_an_unlabelled_retail_callee_at_a_differing_site_is_reported(self):
        ours, retail = calls("a", "size<def*>"), calls("a", "fn_4a1b0")
        target = root_target("root", ours, retail, {"a", "size<def*>"}, equivalence())
        self.assertEqual(target.wanted, {"size<def*>": -1})
        (entry,) = target.unlabelled
        self.assertEqual((entry["label"], entry["sites"], entry["resolved"]),
                         ("fn_4a1b0", 1, None))
        # The placements now name the address: the stale label is our callee.
        named = equivalence(address={"size<def*>": 0x4a1b0, "fn_4a1b0": 0x4a1b0})
        target = root_target("root", ours, retail, {"a", "size<def*>"}, named)
        self.assertEqual(target.wanted, {})
        self.assertEqual(target.unlabelled[0]["resolved"], "size<def*>")

    def test_the_warning_suggests_a_unit_delink(self):
        target = root_target("root", calls("size<def*>"), calls("fn_4a1b0"),
                             {"size<def*>"}, equivalence())
        roots = [dict(symbol="root", cb=100, sites=[dict(symbol="size<def*>")])]
        (row,) = inline_model.site_divergences(roots, mock.Mock(for_root=lambda r: target))
        lines = []
        with mock.patch.dict(os.environ, {"HOMM3_IMAGE": "h3maped"}):
            inline_model._print_target_notes(row, write=lines.append, unit="GameMap")
        self.assertIn("unlabelled fn_4a1b0", lines[-1])
        self.assertIn("`homm3 --image h3maped delink --unit GameMap`", lines[-1])


def site(cb, name, budget=1000):
    return dict(depth=1, budget=budget, remaining=3, cb=cb, running=400, symbol=name,
                owner="root", budget_allows=cb <= 40 or budget >= cb)


class SiteCheckTests(unittest.TestCase):
    # x, a, x all refused; retail expands the second x only.
    ROOT = dict(symbol="root", cb=400, initial_budget=1000,
                sites=[site(2000, "x"), site(2000, "a"), site(2000, "x")])

    def setUp(self):
        self.model = Model.build(self.ROOT)
        self.target = root_target("root", calls("x", "a", "x"), calls("x", "a"),
                                  {"x", "a"}, equivalence())
        self.search = Search(self.model, self.target.wanted, seconds=5,
                             group=self.target.group)
        self.search.sites = inline_targets.SiteTarget(self.target, self.search.base_order)

    def fit(self, n):
        fit = self.search.evaluate([Edit("remove", None, n)])
        self.assertEqual(fit.distance, 0)
        self.search.site_check(fit)
        return fit

    def test_a_fit_must_change_the_call_retail_changes(self):
        self.assertEqual(self.target.wanted, {"x": -1})
        self.assertTrue(self.fit(2).sites)
        wrong = self.fit(0)
        self.assertFalse(wrong.sites)
        self.assertTrue(wrong.site_notes)
        self.assertLess(self.fit(2).key(), wrong.key())


class GroupedSearchTests(unittest.TestCase):
    def test_counts_are_compared_per_class(self):
        root = dict(symbol="r", cb=400, initial_budget=1000,
                    sites=[site(300, "xa"), site(2000, "xb")])
        model = Model.build(root)
        # Retail calls xa's twin xb's body one fewer time: lowering xb's
        # cost below its budget fits only when xa and xb are one class.
        grouped = Search(model, {"xa": -1}, group={"xb": "xa"}, seconds=5)
        self.assertEqual(grouped.evaluate([Edit("callee", symbol="xb", amount=-1500)]).distance, 0)
        alone = Search(model, {"xa": -1}, seconds=5)
        self.assertEqual(alone.evaluate([Edit("callee", symbol="xb", amount=-1500)]).distance, 2)


if __name__ == "__main__":
    unittest.main()
