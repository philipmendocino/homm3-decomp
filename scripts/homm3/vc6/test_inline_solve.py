"""The inline hypothesis search and the site -> source mapping."""
import unittest

from homm3.vc6 import il_cost, inline_replay, inline_sites, inline_solve
from homm3.vc6.inline_solve import Edit, Model, Search


def site(depth, budget, remaining, cb, running, name, **extra):
    return dict(depth=depth, budget=budget, remaining=remaining, cb=cb, running=running,
                symbol=name, owner="root", budget_allows=cb <= 40 or budget >= cb, **extra)


# A root whose wrapper (cb 60) expands with a nested budget of
# (1000 - 60) // 2 = 470, so its nested `inner` (cb 300) expands too.
ROOT = dict(symbol="root", cb=400, initial_budget=1000, sites=[
    site(1, 1000, 2, 60, 400, "wrapper"),
    site(2, 470, 1, 300, 460, "inner"),
    site(1, 640, 1, 30, 1060, "free"),
])


class ModelTests(unittest.TestCase):
    def test_model_reproduces_the_trace(self):
        model = Model.build(ROOT)
        self.assertTrue(model.reproduces())
        self.assertTrue(inline_replay.reproduces(ROOT))
        _expanded, counts, incomplete = model.run()
        self.assertEqual(counts, {})
        self.assertFalse(incomplete)

    def test_a_vetoed_site_keeps_only_its_own_charge_and_stays_a_call(self):
        vetoed = dict(ROOT, sites=[dict(s) for s in ROOT["sites"]])
        vetoed["sites"][0]["veto"] = dict(count=20, limit=9, allowance=0, vetoed=True)
        # 1000 - 60: inner's 300 is not charged; the size keeps inner's own cost.
        vetoed["sites"][2].update(budget=940, running=760)
        self.assertTrue(inline_replay.reproduces(vetoed))
        counts, _ = inline_replay.retained_calls(vetoed)
        self.assertEqual(counts, {"wrapper": 1})
        _expanded, counts, _ = Model.build(vetoed).run()
        self.assertEqual(counts, {"wrapper": 1})

    def test_gates_after_the_budget_test_refuse_uncharged(self):
        gated = dict(ROOT, sites=[dict(s) for s in ROOT["sites"][:1]] + [
            site(1, 1000, 1, 30, 400, "free")])
        gated["sites"][0]["context_rejected"] = True
        gated["sites"][0]["remaining"] = 2
        self.assertTrue(inline_replay.reproduces(gated))
        self.assertFalse(inline_replay.traced_expansion(gated["sites"][0]))
        _expanded, counts, _ = Model.build(gated).run()
        self.assertEqual(counts, {"wrapper": 1})

    def test_refused_site_borrows_children_from_another_expansion(self):
        refused = dict(symbol="other", cb=100, initial_budget=1000, sites=[
            site(1, 1000, 1, 1200, 100, "wrapper")])
        model = Model.build(refused, unit_roots=[ROOT, refused])
        self.assertEqual([model.sym[n] for n in model.kids[0]], ["inner"])
        _expanded, counts, incomplete = model.run(cost={0: 60})
        self.assertEqual(counts, {})
        self.assertFalse(incomplete)
        alone = Model.build(refused)
        self.assertTrue(alone.run(cost={0: 60})[2])     # children unknown

    def test_the_veto_is_rejudged_from_the_kept_children(self):
        # Two traced expansions of push_back: with its insert kept the
        # substituted body totals 15 (> 12, vetoed), with it refused 7.
        def veto(total, vetoed):
            return dict(count=total, limit=12, allowance=0, vetoed=vetoed, total=total)
        kept = dict(symbol="a", cb=400, initial_budget=1000, sites=[
            site(1, 1000, 1, 37, 400, "push_back", veto=veto(15, True)),
            site(2, 1000, 1, 64, 437, "insert", veto=veto(15, False))])
        refused = dict(symbol="b", cb=400, initial_budget=1000, sites=[
            site(1, 1000, 40, 37, 400, "push_back", veto=veto(7, False)),
            site(2, 25, 1, 64, 437, "insert")])
        self.assertTrue(inline_replay.reproduces(kept) and inline_replay.reproduces(refused))
        model = Model.build(kept, unit_roots=[kept, refused])
        self.assertTrue(model.reproduces())
        _expanded, counts, _ = model.run()
        self.assertEqual(counts, {"push_back": 1})
        # Refusing the insert (it costs more than push_back's nested budget)
        # lets push_back's copy survive, retail's push_back -1, insert +1.
        args = inline_solve._apply(model, [Edit("callee", symbol="insert", amount=1000)])
        _expanded, counts, _ = model.run(**args)
        self.assertEqual(counts, {"insert": 1})
        self.assertEqual(model.estimated, 0)        # the pattern was traced

    def test_o1_candidacy_limit_removes_a_costlier_callee(self):
        self.assertEqual(inline_solve.candidate_limit(["/O1", "/Ob2"]), 128)
        self.assertEqual(inline_solve.candidate_limit(["/O2"]), 1000)
        model = Model.build(ROOT, limit=128)
        args = inline_solve._apply(model, [Edit("callee", symbol="wrapper", amount=70)])
        _expanded, counts, _ = model.run(**args)
        self.assertEqual(counts, {"wrapper": 1})


class SearchTests(unittest.TestCase):
    def test_free_sites_after_the_wrapper_refuse_its_nested_call(self):
        # Retail keeps `inner` as a call: the nested budget must fall below
        # 300, i.e. (1000 - 60) // (2 + k) < 300 for k >= 2 free sites after
        # the wrapper (inserted before the last site also counts).
        search = Search(Model.build(ROOT), {"inner": 1}, max_insert=4, seconds=10)
        result = search.solve(max_edits=1)
        inserts = [f for f in result["fits"] if f.edits[0].kind == "insert"]
        self.assertTrue(inserts)
        best = inserts[0]
        self.assertEqual(best.edits[0].amount, 2)
        self.assertEqual(best.edits[0].owner, None)
        self.assertEqual(best.span, (0, 1))
        self.assertIsNotNone(best.window)
        self.assertFalse(any(f.edits[0].kind == "callee" for f in result["fits"]))
        wider = Search(Model.build(ROOT), {"inner": 1}, max_insert=4, callee_range=200,
                       seconds=10).solve(max_edits=1)
        # inner must cost more than its nested budget of 470.
        self.assertIn(("inner", 171), [(f.edits[0].symbol, f.edits[0].amount)
                                       for f in wider["fits"] if f.edits[0].kind == "callee"])

    def test_window_reports_how_far_the_root_cost_may_move(self):
        search = Search(Model.build(ROOT), {"inner": 1}, max_insert=4, seconds=10)
        fit = search.evaluate([Edit("insert", None, 0, amount=2)])
        self.assertEqual(fit.distance, 0)
        low, high = search.window(fit, 400)
        # Budget 2 x cb: (2cb - 60) // 4 < 300 holds up to cb 629.
        self.assertEqual(high, 629 - 400)
        self.assertEqual(low, -400)          # the span bounds the search

    def test_nothing_to_do_when_counts_already_agree(self):
        result = Search(Model.build(ROOT), {}, seconds=5).solve()
        self.assertEqual(result["base_distance"], 0)
        self.assertEqual(result["fits"], [])

    def test_removed_site_and_reported_nearest(self):
        # Retail expands a second `big` that our budget refuses: dropping the
        # earlier costly site leaves budget for it.
        root = dict(symbol="r", cb=100, initial_budget=1000, sites=[
            site(1, 1000, 2, 600, 100, "first"),
            site(1, 400, 1, 500, 700, "big")])
        result = Search(Model.build(root), {"big": -1}, seconds=10).solve(max_edits=1)
        kinds = [(f.edits[0].kind, f.edits[0].position) for f in result["fits"]]
        # Dropping the earlier site is smaller than the 450 cb (budget 1100)
        # that would also admit it.
        self.assertEqual(kinds[0], ("remove", 0))
        self.assertIn(("cb", 0), kinds)


class WhatIfTests(unittest.TestCase):
    def test_manual_edits_by_trace_index(self):
        model = Model.build(ROOT)
        changes = inline_solve.what_if(model, adds=[(0, 2)])
        self.assertEqual([(r["index"], r["verdict"], r["after"]) for r in changes["flips"]],
                         [(1, "refused", (1000 - 60) // 4)])
        self.assertEqual(changes["calls"], {"inner": 1})
        removed = inline_solve.what_if(model, removes=[0])
        self.assertEqual(removed["calls"], {})          # the removed call is gone
        cheaper = inline_solve.what_if(model, costs=[("inner", 600)])
        self.assertEqual(cheaper["calls"], {"inner": 1})
        with self.assertRaises(ValueError):
            inline_solve.what_if(model, removes=[9])


def tup(op, *operands):
    return il_cost.Tuple(0, op, op != il_cost.INFO_OP, list(operands))


def line(number):
    return tup(il_cost.INFO_OP, il_cost.INFO_LINE, number)


class SourceMapTests(unittest.TestCase):
    NAMES = {1: "?outer@@YAHH@Z", 2: "?inner@@YAHXZ", 3: "??0T@@QAE@XZ",
             4: "??1T@@QAE@XZ", 5: "?g_data@@3HA"}

    def calls(self, tuples):
        body = il_cost.Body(0, tuples)
        functions = {"?outer@@YAHH@Z", "?inner@@YAHXZ", "??0T@@QAE@XZ", "??1T@@QAE@XZ"}
        return inline_sites.call_sites(body, self.NAMES, functions.__contains__)

    def test_a_call_is_listed_when_its_arguments_complete(self):
        # outer(inner()) on line 10: addr outer, call, addr inner, call, end, arg, end
        calls = self.calls([line(10), tup(0x26, ("sym", 1)), tup(0x3E),
                            tup(0x26, ("sym", 2)), tup(0x3E), tup(0x4C), tup(0x55),
                            tup(0x4C), line(11), tup(0x26, ("sym", 5)), tup(0x30)])
        self.assertEqual([(c["callee"], c["line"]) for c in calls],
                         [("?inner@@YAHXZ", 10), ("?outer@@YAHH@Z", 10)])

    def test_temporary_destructors_run_at_the_cleanup(self):
        calls = self.calls([line(5), tup(0x26, ("sym", 3)), tup(0x3E), tup(0x4C),
                            tup(0x26, ("sym", 4)), tup(0x3E), tup(0x4C), tup(0x5C),
                            tup(0x26, ("sym", 2)), tup(0x3E), tup(0x4C), tup(0x5D)])
        self.assertEqual([c["callee"] for c in calls],
                         ["??0T@@QAE@XZ", "?inner@@YAHXZ", "??1T@@QAE@XZ"])

    def test_alignment_places_a_subsequence_and_tolerates_misses(self):
        calls = [dict(callee=name) for name in ("a", "b", "c", "b", "d")]
        self.assertEqual(inline_sites.align(["b", "d"], calls), [1, 4])
        self.assertEqual(inline_sites.align(["a", "x", "d"], calls), [0, None, 4])
        self.assertEqual(inline_sites.align([], calls), [])


if __name__ == "__main__":
    unittest.main()
