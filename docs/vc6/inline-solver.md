# Inline-wall hypotheses: `predict-inline --tu UNIT --solve`

Many non-exact functions are inline-budget walls: our VC6 makes different
expand/call decisions than retail. `predict-inline --tu UNIT` traces every
budget test C2 makes in a unit, the budget replay
(`homm3.vc6.inline_replay`, [inliner.md](inliner.md#the-replay-homm3vc6inline_replay))
reproduces them, and retail's object shows which traced callees retail keeps
as calls. `--cb-window` asks whether the root's own cost alone explains the
difference; for most large walls it does not ("window: none"), so retail's
site set or callee costs differ from ours.

The solver answers **where** and **what kind** of source difference would
reproduce retail's decisions, so a matcher can look for native evidence at
that place (Mac retained calls, Loki or Dreamcast structure, retail call
order). A hypothesis is not source to adopt: the project forbids
budget-steering code (AGENTS.md, helper boundaries and inlining). It turns
"the compiler disagrees somewhere in this 3000-tuple function" into
"retail's helper body has one fewer call", "two free accessor calls follow
line 286", or "only a library cost change fits, so look for a different
boundary".

```sh
# trace and solve every diverging root of a unit
homm3 --image h3maped vc6 predict-inline --tu Monster --solve
# one root, from a saved trace (no compile)
homm3 vc6 predict-inline --tu town --json > build/town-trace.json
homm3 vc6 predict-inline --tu town --load build/town-trace.json --solve --root '?load@town@@'
# --max-edits 1|2  --max-insert K  --top N  --seconds S  --json  --json-out FILE
# check one hypothesis by hand (trace indices from `--root` alone):
homm3 vc6 predict-inline --tu town --load build/town-trace.json --root '?load@town@@' \
    --add 12:2 --remove 7 --cost '?getMapItem@:41' --cb 990
```

The manual what-if goes through the same model as the search (template
children, gates, veto votes): `--add INDEX:COUNT` inserts free sites after
that one site in its own list, `--remove INDEX` drops a site, and
`--cost CALLEE:CB` sets every matching callee's cost. It prints each changed
decision with its old and new budget and the net change in retained calls
per callee.

## What it searches

For one root, the observable target is the per-callee change in retained
out-of-line calls, retail minus ours, over the traced callees (the same rows
`predict-inline --tu` lists). A hypothesis fits when its replay changes
every traced callee's count by exactly that amount and no other. The edit
kinds:

| edit | meaning in source |
|---|---|
| `cb` | the root's own cost (budget `clamp(2 x cb, 1000, 35000)`): more or fewer statements in the caller |
| `insert` in the root's list | K free (cb <= 40) candidate calls after a site: accessor or small-helper calls the original caller has |
| `insert` in a callee's list | the same inside a helper's body, which changes every expansion of that helper |
| `remove` | a site retail does not have: a helper call our source makes, or a call inside a helper body |
| `callee` | a callee's cost moved by d at every site (a lighter or heavier helper body). Under /O1 a cost reaching 128 makes it a non-candidate (a call that no longer counts as a remaining site); so does a cost reaching C1XX's save limit 175 for an auto-inline body (gl flag 0x80 clear: not declared inline, not in-class, not a template) |

The search is exhaustive over single edits (every position of every site
list, insert counts 1..K, callee deltas within +-48 of each traced callee),
then pairs among the 40 best partial singles and the fits (a pair is kept
only when it beats both of its edits alone), then each of those singles
with a root-cb shift (an edit that adds or drops calls moves the caller's
own cost too). `--max-edits 3` adds a greedy third edit to the eight best
pairs. Each search stops at `--seconds` and says so.
A replay is a few hundred microseconds to a few milliseconds, so ordinary
roots finish in under a second; the largest (tens of thousands of nested
sites) hit the time limit in the pair stage.

Ranking is by fewest edits, then: a removal of a site of a callee whose
count must change ranks last (removing the call trivially changes its
count); edits of a library body (the pinned compiler's headers or vendor
code, which the project keeps pristine) rank after source edits and are
labelled "not a source lever"; fits whose newly expanded sites have unknown
children rank after complete ones; then whether the fit holds at the current
root cb; then magnitude (an inserted site counts 1, a removal 1, a cost
delta d counts d/8).

Fits that differ only in an insertion's position, its count, or a callee's
delta are merged into a range: "add 2..5 free sites after #12 ... or as late
as after #15; before #16" means any placement in that run works.

Every fit carries the **root-cb window** over which it still holds. Real
edits move the caller's cost (an accessor call costs a handful of tuples),
so a fit that holds only at exactly the current cb is fragile; a wide window
is robust.

When nothing small fits, the report lists the nearest hypotheses and, per
wanted callee, the edits that give exactly that callee's change without
moving any other callee away from retail. Walls often decompose: one piece
is a helper boundary, another a missing accessor, a third a retail label
difference (ICF-folded labels make a -1/+1 pair that no budget edit can
fix; read those by hand, as `predict-inline --tu` already warns).

## Site to source

Each traced site list is one body's candidate calls in C2's order, and the
trace keeps the IL streams both C2 runs consumed
(`<trace directory>/capture`), so sites map to the source C1XX saw
(`homm3.vc6.inline_sites`):

- a direct call is `addr <callee>`, its this-expression, `call`, its
  arguments and `end`. C2 lists a site when its call completes, so an
  argument's calls precede the call that takes them, and `a << b << c`
  lists the inner `<<` first;
- a completed call followed by a `dtor` tuple is a temporary's destructor;
  it runs at the full expression's `cleanup`, latest construction first;
- the call's line is the last line-number info tuple before it (C1XX
  records a multi-line statement at its last line, so an `if` condition
  spanning lines is reported on the line with its `{`);
- the traced symbols are aligned with the call tuples by a longest common
  subsequence, since traced sites are a subsequence of the calls
  (non-candidates and pre-budget rejections are not traced). Over the 778
  site lists of h3maped `Monster` 771 align exactly; an unaligned site is
  shown with `~` at the previous aligned call. Virtual calls have no callee
  address and can take an outer call's; the alignment absorbs that.

A nested site is reported with its callee-body line and its root ancestor:
`#6 std::string::max_size (xstring:548), under root site #1
std::string::operator= (src/town.cpp:287 'm_name = g_text;')`.

## Children of refused sites

A site the trace refused has unknown children, which matters when a
hypothesis expands it. The model borrows them from the largest traced
expansion of the same callee anywhere in the unit (a callee's candidate
sites come from its one stored body), recursively up to six levels, and
predicts the veto of a newly expanded site from that callee's other veto
records. A fit that still expands a site with unknown children is marked
`[incomplete]`; confirm it by compiling.

## The veto is a lever under /O1

Across the 27,423 veto records of the h3maped rmg, GameMap, Monster and
MapView traces the verdict is exactly `total > limit`, where `total` is the
substituted body's whole weighted count. That total depends on which of the
callee's own sites were kept: `vector<TRmgMapPosition>::push_back` totals 7
with its nested `insert` refused and 15 with it expanded, against a limit of
12. So a nested refusal can save the outer copy, and retail's typical
editor pattern "push_back expanded, insert called" is a veto outcome, not a
budget one.

The model re-judges each veto after its children are decided: a children
pattern traced anywhere in the unit gives its total directly; an untraced
one is estimated from a traced pattern plus per-child increments measured
on traced pairs that differ in that child alone (flagged `[veto estimated]`);
with neither, the traced verdict stands. The increment is not the child's
own total (insert's 15 adds 8 to push_back's), so it is measured, never
derived. Example, h3maped `type_random_map_generator::addObject`: five
free sites anywhere after `costs.push_back(0);` (src/rmg.cpp:6510) refuse
both nested inserts, both push_back copies survive the veto, and four of
retail's five call differences follow; the fifth, one more expanded
`~vector<int>`, is a separate veto.

## Replay fidelity

The replay must reproduce the trace before any what-if means anything.
`predict-inline --tu` prints the count. Three rules were missing for the
map editor's /O1 profile (all read from C2.DLL; see the ledger in
[inliner.md](inliner.md#3-address-ledger)):

1. **Veto spending.** Under /O1 (option bit23 clear) the post-substitution
   veto `0x94964` can revert an expanded copy after its body was expanded.
   The site keeps its own cost charge, but its body's spending is charged
   neither to the enclosing budgets nor to the running size, and the call
   stays. `TRmgZone::~TRmgZone`'s vetoed point-vector destructor (cb 94)
   leaves the second one budget 906 = 1000 - 94, not 857.
2. **The argument-count gate is tested after the budget.** A mismatch
   clears ECX in the cold block `0x93d32` and falls through; C2 tests it at
   `0x19faf`, after the budget and running-size tests, so the site is
   budget-tested and refused uncharged.
3. **The statement-context refusal.** A call tuple with byte `+0x12` set
   takes the alternate substitution path `0x19ae0` after those tests; it
   walks the call's statement and refuses, uncharged, at `0x93d62` on a
   class-0x12 tuple with opcode 0xde/0xdf/0x184 or an 0x1aa tuple. The
   editor's common case is an `auto_ptr` copy constructed into a by-value
   argument (`_placeObject(auto_ptr<TGameObject>(...), ...)`) and calls
   such as `getPDocument()` inside such argument lists.

The shim logs the gates: `args=` (ECX at the budget hook) and `alt=` (the
call tuple's byte +0x12) on each site record, and a `reject root= callee=
why=context` record from a hook at `0x93d62` that replays the original
`test` so the following `jz` sees its flags. Measured on fresh traces of the
same sources, before (the committed replay) and after:

Map editor (h3maped, /O1), every unit whose replay changed:

| unit | before | after |
|---|---|---|
| Army | 47/53 | 53/53 |
| artifact | 25/33 | 33/33 |
| ArtifactsDlg | 87/101 | 101/101 |
| Colors | 31/34 | 34/34 |
| DIBSection | 59/63 | 63/63 |
| EditArtifactDlg | 42/50 | 50/50 |
| GameMap | 1012/1255 | 1255/1255 |
| GUIGameObject | 750/779 | 779/779 |
| Hero | 301/337 | 337/337 |
| HeroPropsGeneralPage | 160/169 | 169/169 |
| HeroPropsSpellsPage | 50/53 | 53/53 |
| HeroPrototypePropsGeneralPage | 92/102 | 102/102 |
| MainFrame | 107/117 | 117/117 |
| MapDoc | 275/307 | 307/307 |
| MapEditWnd | 180/194 | 194/194 |
| MapSpecsPlayerSpecsPage | 74/85 | 85/85 |
| MapView | 392/421 | 421/421 |
| Monster | 118/130 | 130/130 |
| MonsterPropsTreasurePage | 41/43 | 43/43 |
| objecttype | 254/298 | 298/298 |
| ObjectTypeTable | 25/27 | 27/27 |
| Options | 30/32 | 32/32 |
| QuestLocation | 164/188 | 188/188 |
| RandomGeneratorPropsDlg | 139/150 | 150/150 |
| random_object_placer | 40/48 | 48/48 |
| rmg | 355/461 | 461/461 |
| rmg_terrain | 101/109 | 109/109 |
| rmg_voronoi | 20/24 | 24/24 |
| SpellsDlg | 45/52 | 52/52 |
| TileHRuler | 98/105 | 105/105 |
| TileVRuler | 97/104 | 104/104 |
| TimedEvent | 101/117 | 117/117 |
| Town | 176/197 | 197/197 |
| **all 35 units** | **5699/6449** | **6449/6449** |

Unchanged and already complete: cspriteframe, resourcemanager.

Game (/O2):

| unit | before | after |
|---|---|---|
| customcampaign | 373/375 | 375/375 |
| game | 508/510 | 510/510 |
| singleselectionwindow | 432/438 | 438/438 |
| **all 21 units** | **4121/4131** | **4131/4131** |

Unchanged and already complete: adventuremapwindow, advmgr, ai_player, artifact, command, events, hero, kb, mapcell, multiplayerwindow, overview, philai, rmg, seerhut, spells, town, townmgr, viewwrld.

The game's /O2 profile sets bit23, so it has no veto; its few remaining
failures were all statement-context refusals. In every traced unit the
refused callee is a copy constructor building a by-value argument or
temporary (`auto_ptr<TGameObject>`, `std::string`, `std::locale`, `hero`,
`GameSelectionHeadersStruct`, `_Temp_iterator`), or an accessor called
inside such an argument list (`TRefCountingPtr::operator->`, vector
indexing, `getPDocument()`); no argument-count mismatch was observed. The
search model (`inline_solve.Model`, which also re-judges vetoes) reproduces
the same 10,580 roots, decisions and veto verdicts.

## Limits

- Only traced callees are compared. Calls C2 rejects before the budget
  test (depth, caller EH state, `0x100` allocating callees in non-EH
  bodies) never change, and a callee retail reaches under an ICF-folded
  label of another name counts as a difference no edit can fix.
- A removed or inserted site's own cost is not added to the root cb; the
  window shows how much cb movement each fit tolerates instead.
- Callee edits change every site of that callee in the root; a helper whose
  cost differs per overload is a separate symbol.
- Search size: single edits are complete within the stated ranges; pairs
  only combine the best 40 partial singles, so a two-edit fix whose halves
  each look useless alone can be missed. "No fit" means none within these
  bounds, not none at all.
- The save limit is read from the trace's own IL flags, so it applies only
  to callees whose bodies the unit compiles.

Tests: `python3 -m unittest homm3.vc6.test_inline_solve
homm3.vc6.test_inline_replay homm3.vc6.test_inline_trace`.
