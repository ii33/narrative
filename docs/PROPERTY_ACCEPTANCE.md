# Property state acceptance matrix

Status: deterministic v5 implementation checked locally; a minimal synthetic
live property closure passed on 2026-10-19, and a chapter-scale three-chapter
closure passed on 2026-10-20. Full-work property extraction remains unverified.
This file records the expected synthetic example and the local verification
below.

All cases use fresh isolated projects and the contract in
`PROPERTY_STATE_CONTRACT.md`. `holder` is an item property with a single node
reference; `held_items(person)` must be an inverse query, never a stored second
truth. A and B are independent branches between E0 and E9.

| Case | Setup | Required observation |
| --- | --- | --- |
| Initial holding | key.initial holder=Alice | At the initial state, key holder is Alice; Alice's held-items query includes key. |
| Successful transfer | E1.end sets key holder=Bob, expected Alice | After E1, holder is Bob; before E1 it remains Alice. No separate Alice/ Bob inventory writes. |
| Failed transfer | E2 is recorded as an event but has no holder effect | Holder remains Bob after E2; no false transfer record. |
| Baseline observation | At an explicit later time, observe lamp holder=Carol | Before that observation, state is unrecorded; at the observation it is Carol. No invented acquisition event. |
| Flashback entry | Enter an earlier reviewed E0 effect after E1 was entered | Query recomputes from story order rather than insertion order; history retains both submissions. |
| Independent branches | A1 and B1 set the same holder to different people; both precede E9 | E9 state reports conflict with both supporting records. No ID, visual-slot, or commit-order winner. |
| Partial order | Query at A1 when B1 is incomparable | Result explicitly reports uncertainty or conflict rather than treating B1 as later. |
| Atomic batch | One event batch updates two properties; second effect violates type or expected state | Precheck/accept fails; neither property changes and no accepted batch is written. |
| Retraction | Retract a mistaken accepted effect | Current projection rebuilds without the effect; original and retraction remain in history. |
| Cell distinctions | Use explicit unknown, explicit absent, known empty collection, and no record | Four distinct query results survive serialization. |
| Round-trip and purity | Serialize/deserialize records and import the core with Qt/SQLite/model modules absent | Equal query results; no GUI, model, MCP, or database imports from the pure core. |
| CLI/Qt consistency | Query the same node/property/time through CLI and Qt | Same state, supporting IDs, and explanation as `Operations`. |
| Old format gate | Open a v4 file or propose an ordinary v4 attribute/relation assertion | Explicit version/contract error; no migration, mutation, or silent reinterpretation. |

Real compatible-model acceptance is tracked separately from these deterministic
checks. A transport failure, truncated output, or missing candidate is recorded
as incomplete rather than counted as a pass.

## Local verification (2026-10-18)

The v5 graph and property order group completed with **65 passed**. The Qt
group completed with **12 passed, 0 skipped**, and the graph-agent group with
**23 passed**. Ruff passed for `src/narrative` and `tests`; Pyright reported
zero errors for the changed property core, store, order, Operations, agent,
and CLI paths. The persistent example was built at
`data/graph-projects/property-story-demo.sqlite3`; its manifest records
`EXPECTED_STATE` for the failed transfer, an unchanged holder, branch conflict,
and a retraction that rebuilds the projection.

A full repository run outside the sandbox, where Streamlit's local asyncio
socketpair can initialize, completed with **806 passed, 21 failed, 0 skipped**
in 247.47 seconds. Twenty failures are older Streamlit UI tests that still
look for retired tabs, buttons, or metrics after the graph workbench replaced
that page. The other failure exposed a direct model-catalog import in legacy
`ui.py`; that lookup now goes through `Operations`, and the entry-point test
now recognizes the `graph_ui.py` UI adapter. The affected Operations test
file subsequently completed with **4 passed**, but the complete suite has
not been rerun after this change. The twenty older UI failures remain, so the
repository suite is not green. The four newly migrated Streamlit test files
completed with **20 passed**.

After Streamlit work was deferred, the non-Streamlit deterministic suite ran
with fifteen files containing Streamlit UI tests excluded. It completed with
**714 passed, 0 failed** in 191.46 seconds. This is a file-level exclusion:
some non-UI tests in those mixed files were also omitted. The focused native
Qt files (`test_native_app.py`, `test_native_bridge.py`, and
`test_native_property.py`) separately completed with **12 passed**. No skipped
tests were added to conceal the older Streamlit failures.

Reproduction command from the repository root (with the approved local test
environment that permits Windows asyncio's local socket pair):

```powershell
$ignore = @('test_analysis_ui.py','test_builtin_texts.py','test_complete_ui.py','test_context_settings_ui.py','test_extraction_capacity.py','test_graph_ui.py','test_harness.py','test_model_events_ui.py','test_providers.py','test_recovery_ui.py','test_review_workflow.py','test_time_query_ui.py','test_timeline_ui.py','test_ui.py','test_usage.py') | ForEach-Object { "--ignore=tests/$_" }
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q --tb=short --basetemp=.pytest-v5-non-streamlit-external @ignore
```

The persistent demo was also opened in both offscreen and normal Qt windows;
both paths loaded the property page and returned `key.holder = mira`. A clear
offscreen screenshot is at `artifacts/property-state/property-workspace.png`.
The persistent demo's CLI `property-state`, `property-history`, and
`property-world-state` commands were rerun. The current holder is `mira`
with `branch-a-change` as the supporting record; history still contains
`branch-b-change` and its accepted retraction.
Two earlier bounded DeepSeek attempts for the v4 round ended in gateway
`TRANSPORT` failures; those are separate from the v5 result below. OpenCode Go
DeepSeek used for implementation is separate from narrative-agent acceptance.

## Three-chapter real-model run and follow-up

The exact first three chapters of *Piaomiao Zhi Lü* (16,371 characters,
verified source hash and chapter boundaries) were sent once through the
project's DeepSeek Harness runtime to Ark Coding Plan `deepseek-v4.1-flash`.
The isolated `graph-agent/10` run is recorded at
`data/example-run/report.json`, with original responses in its
`graph_analysis_runs/<run_id>/journal.json` and a separate read-only quality
review in `quality-review.md`. It completed 18 requests with 1,043,602
reported tokens, inside the user-approved 32-round / 3,000,000-token budget.
The non-private connection and output-cap probes consumed another 231 tokens,
excluded from the formal run's usage. All 18 generation requests set
`max_tokens=160000`; actual maximum completion was 14,222 tokens.

The model produced one precheck-valid **pending graph candidate** with 23 event
nodes, 21 acyclic `before` assertions and 104 located evidence records across
the three chapters. It attempted 18 property records, but `property_precheck`
returned 48 diagnostics and **no property candidate**. The formal graph and
property state were not accepted or changed. This is a partial business result,
not a successful node-property acceptance.

The follow-up code fix makes the two-stage review gate explicit and loads
source text internally for strict property evidence validation. It reports
`PENDING_GRAPH_REVIEW` when property records reference unaccepted graph draft
nodes; it does not relax references or accept the real graph candidate. The
prompt version is now `graph-agent/11`; **no second novel request** was made
with that version. Synthetic property/agent tests passed 50/50, and the final
non-Streamlit file-excluded suite passed **727/727** in 233.23 seconds. Ruff,
Pyright and diff checks passed. Streamlit UI was retired on 2026-10-20; its
AppTest files are skipped by `tests/conftest.py`, and the Qt Quick desktop is
the only product entry.

## Minimal live property closure (2026-10-19, graph-agent/11)

A bounded, isolated run used a 103-character synthetic three-chapter source and
a human-authored graph fixture (five person/item/location nodes and five event
nodes, **no property records**). The fixture was accepted through `Operations`
before the model started. The model was asked to extract only `key.holder` and
`linzhou.location`.

The first run produced no candidate: `property_precheck` passed with five
records, but `property_propose` was rejected because the model passed a
`source` argument that the agent bridge did not allow. The public schema
advertised `Operations.property_propose(records, expected_revision, source,
note)`, so this was a bridge/contract mismatch, not a semantic model error. The
bridge now accepts `source` and still fixes provenance to `model`; the schema
lists the agent tool arguments explicitly. A regression test was added.

The single fixed rerun produced one pending candidate with five records.
Independent `property_precheck` returned zero diagnostics, and all three quotes
occur exactly once. The user accepted the candidate through
`Operations.property_accept`; the formal revision moved from 2 to 3. Post-accept
queries returned `key.holder = shenyan` at `e_transfer.end`, current
`key.holder = shenyan`, and `linzhou.location = station`.

The run itself ended `incomplete` after hitting its eight-round budget one round
after proposing; the candidate is still valid and was reviewed separately. The
fixture has no event occurrence or `before` assertions, so a query at
`e_transfer.start` reports `conflict` rather than guessing the pre-transfer
state. That is an honest projection over missing order evidence, not a pass.
This 103-character synthetic closure is **not** a work-quality, recall, or
full-chapter baseline. Details are in
`data/property-agent-11-live-20261019T2015Z/acceptance.md`; the first run's
root cause is in `data/property-agent-11-live-20261019T1955Z/root-cause.md`.

## Chapter-scale three-chapter closure (2026-10-20, graph-agent/11 + Ark)

The existing graph-agent/10 three-chapter candidate (263 objects: 23 events,
62 nodes, 47 assertions, 104 evidence records, 0 diagnostics) was accepted by a
human as the reviewed graph fixture; revision 1→2. A property-only run then
used Ark `deepseek-v4.1-flash` with no total round or token ceiling.

Run 1: 15 requests, 589,089 tokens, 24 property records, `property_precheck`
0 diagnostics; the run ended `incomplete` because the model repeated
no-progress actions after proposing. Semantic review found three high-risk
records (a plan treated as location, “fusion” treated as holder, knife
position treated as holder) and three medium-risk records. Because
`property_accept` is atomic, the whole batch was rejected.

Run 2 added explicit semantic constraints: speech/plan/intention is not world
state; fusion or embedding is not holder; holder evidence must directly show
possession or transfer; location must be actual at the event boundary; uncertain
records are omitted. It produced 12 records with 0 diagnostics in 12 requests
and 322,497 tokens. The three high-risk records disappeared. Two medium risks
remained (a bracelet quote that does not name 纳戒; Fushan's lake location
inferred from event context). The user accepted the batch; revision 2→3.

Post-accept queries: `i_tanbo.holder = p_liqiang`; `p_liqiang.location` at
`e_lake.start = l_sehu` and at `e_confront.start = l_hotel`;
`p_fushan.location` at `e_fushan_appear.start = l_sehu`.

This is the first chapter-scale property candidate to pass precheck and be
accepted. It is one three-chapter sample, not a work-quality or recall baseline.
The stricter task reduced coverage from 24 to 12 records; model output quality
and the audited result remain separate. Details are in
`data/example-run/property-acceptance-20261020/`.
