"""homm3.vc6.inline_replay - replay C2's /Ob2 budget rule over a traced root.

Pure and compiler-free. The rule, validated against every root of a full
`predict-inline --tu` trace (game rmg 497/497, town 90/90 and, with the
veto, editor rmg 461/461 reproduce each
recorded budget, verdict and running size):

- every site list (the root's own sites, and each expanded body's) keeps a
  running budget; a site costing over 40 is refused when that budget is
  below its cost, and otherwise its cost is charged to every enclosing
  running budget;
- an expanded site's body starts with (budget - cost if over 40) divided
  by the site's remaining-sibling count, itself included;
- the root's size starts at its cb; every expanded site adds its cost, and
  a site costing over 40 adds it once more for each enclosing expansion;
- once that size reaches 35000 nothing further expands, although the
  remaining sites are still budget-tested;
- two gates follow the budget and running-size tests, so a site they
  refuse is budget-tested and then refused without a charge: an argument
  count that does not match the callee's formals (cleared at 0x93d32,
  tested at 0x19faf), and the statement-context walk of a call whose
  tuple takes the alternate substitution path (0x19ae0, refusing at
  0x93d62). The trace records both;
- under /O1 (option bit23 clear) the post-substitution veto `0x94964` can
  revert an expanded copy after its body was expanded: the site keeps its
  own charge, but the budget its body spent is charged neither to the
  enclosing budgets nor to the running size, and the call stays. The
  replay takes each site's verdict from the traced veto record.

Children of a site the trace refused are unknown, so a what-if that turns
a refusal into an expansion counts the site's own cost only.
"""
from __future__ import annotations

SIZE_CAP = 35000


def _admitted(site: dict) -> bool:
    """The budget-independent gates C2 applies after the budget test: the
    argument count (0x19faf) and the statement context (0x93d62)."""
    return site.get("args_match", True) and not site.get("context_rejected", False)


def traced_expansion(site: dict) -> bool:
    """Whether C2 expanded a traced site: the budget and running-size tests
    passed and so did the gates C2 checks after them (recorded since the
    shim logs them)."""
    return site["budget_allows"] and site["running"] < SIZE_CAP and _admitted(site)


def tree(sites: list[dict]) -> list[dict]:
    """Nest a root's sites (in trace order) by depth."""
    root: dict = {"children": []}
    stack = [(0, root)]
    for site in sites:
        node = dict(site, children=[])
        while stack[-1][0] >= site["depth"]:
            stack.pop()
        stack[-1][1]["children"].append(node)
        stack.append((site["depth"], node))
    return root["children"]


def _vetoed(node) -> bool:
    veto = node.get("veto")
    return bool(veto and veto["vetoed"])


def _replay(nodes, budget, out, state) -> int:
    """C2's expandInlineCalls over one site list; returns the budget spent.

    An expanded site charges its own cost (over 40 only) and then the
    budget its body spent, unless the post-substitution veto (`0x94964`,
    active under /O1) reverted the copy: a vetoed site keeps its own
    charge, while its body's spending reaches neither the budget nor the
    running size."""
    left = budget
    for node in nodes:
        cost = node["cb"]
        allows = cost <= 40 or left >= cost
        expands = allows and state["size"] < SIZE_CAP and _admitted(node)
        out.append(dict(site=node, budget=left, size=state["size"],
                        budget_allows=allows, expands=expands,
                        vetoed=expands and _vetoed(node)))
        if not expands:
            continue
        charge = cost if cost > 40 else 0
        nested = (left - charge) // node["remaining"]
        left -= charge
        state["size"] += cost
        spent = _replay(node["children"], nested, out, state)
        if not _vetoed(node):
            left -= spent
            state["size"] += spent
    return budget - left


def replay(root: dict, *, cb: int | None = None,
           extra_sites: dict[int, int] | None = None) -> list[dict]:
    """Decisions for one traced root.

    `cb` overrides the root's estimate (its budget is clamp(2 x cb, 1000,
    35000)); `extra_sites` maps a trace index to a number of free sites
    inserted directly after that site in the same site list, which raises
    the remaining count of that site and of its earlier siblings."""
    sites = [dict(site, index=index) for index, site in enumerate(root["sites"])]
    for index, count in (extra_sites or {}).items():
        depth = sites[index]["depth"]
        for earlier in range(index, -1, -1):
            if sites[earlier]["depth"] < depth:
                break
            if sites[earlier]["depth"] == depth:
                sites[earlier]["remaining"] += count
    cb = root["cb"] if cb is None else cb
    out: list[dict] = []
    _replay(tree(sites), min(35000, max(1000, 2 * cb)), out, {"size": cb})
    return out


def reproduces(root: dict) -> bool:
    """True when the replay reproduces every recorded budget test."""
    return all(row["budget"] == row["site"]["budget"]
               and row["budget_allows"] == row["site"]["budget_allows"]
               and row["size"] == row["site"]["running"]
               for row in replay(root))


def retained_calls(root: dict, *, cb: int | None = None) -> tuple[dict, bool]:
    """Per-callee count of reached sites the replay leaves as calls (refused
    or vetoed), plus whether a site the trace refused now expands (its
    unknown children are missing). A vetoed copy is discarded, so its
    nested sites are not reached."""
    counts: dict[str, int] = {}
    incomplete = False
    stack: list[tuple[int, bool]] = []
    for row in replay(root, cb=cb):
        site = row["site"]
        while stack and stack[-1][0] >= site["depth"]:
            stack.pop()
        kept = row["expands"] and not row["vetoed"]
        if all(alive for _, alive in stack):
            if not kept:
                counts[site["symbol"]] = counts.get(site["symbol"], 0) + 1
            elif not traced_expansion(site):
                incomplete = True
        stack.append((site["depth"], kept))
    return counts, incomplete


def cb_windows(root: dict, wanted: dict[str, int], low: int, high: int) -> list[dict]:
    """Ranges of root cb in [low, high] whose replay changes each wanted
    callee's retained-call count by exactly its delta and leaves every
    other traced callee's count unchanged.

    `wanted` maps callee -> (retail calls - our calls). Windows marked
    incomplete expand a site the trace refused; confirm them by compiling."""
    base, _ = retained_calls(root)
    out: list[dict] = []
    for cb in range(max(1, low), high + 1):
        got, incomplete = retained_calls(root, cb=cb)
        names = set(base) | set(got)
        if any(got.get(k, 0) - base.get(k, 0) != wanted.get(k, 0) for k in names | set(wanted)):
            continue
        if out and out[-1]["high"] == cb - 1 and out[-1]["incomplete"] == incomplete:
            out[-1]["high"] = cb
        else:
            out.append(dict(low=cb, high=cb, incomplete=incomplete))
    return out
