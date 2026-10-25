# Node property and event change contract (v5)

Status: implemented in the v5 project path and verified by the focused local
suite. Live compatible-model acceptance remains incomplete.

This contract replaces ordinary `attribute`, `relation`, and `participation`
assertions in new graph projects. Stable graph node IDs remain the identity of
people, items, places, and events. Temporal and containment assertions continue
to describe event order and reading organization. Opening a v4 project as v5
must fail explicitly; neither opening nor failure may alter the old file.

## Authoritative records

The following JSON records are append-only after human review. `id` is stable
and unique. They are not a second mutable copy of node state.

| Kind | Required fields | Meaning |
| --- | --- | --- |
| `property_definition` | `id`, `value_type`, `cardinality`, `persistence` | `value_type` is `string`, `number`, `boolean`, or `node`; `cardinality` is `one` or `many`; `persistence` is `until_changed` or `instant`. Optional `owner_node_types` and `target_node_types` constrain references. |
| `property_initial` | `id`, `node_id`, `property_id`, `state` | Initial local state for one node property. At most one active initial record per pair. |
| `property_observation` | `id`, `node_id`, `property_id`, `at`, `state` | Baseline observed at a story `TimeRef`; observation does not assert an acquisition or other change event. |
| `event_change` | `id`, `event_id`, `boundary`, `effects` | One atomic event batch at `start` or `end`. Each effect names `node_id`, `property_id`, `op`, optional `value`, optional `expected`. |
| `property_retraction` | `id`, `target_id`, `reason` | System correction of an earlier property record; history remains readable and projections rebuild without that record. |

`state` is a tagged cell: `{"kind":"known","value":...}`,
`{"kind":"unknown"}`, or `{"kind":"absent"}`. The absence of any applicable
record is returned as `unrecorded`, never as `unknown` or `absent`. A known
multivalue `[]` is a recorded empty collection and differs from all three.
`value` of a node reference is its stable node ID; a multivalue property uses a
list of unique typed values. `target_node_types` applies only to node references.

Effect `op` is `set`, `add`, `remove`, `unknown`, or `absent`. `set` replaces a
single value or whole collection. `add` and `remove` require a multivalue
definition. Optional `expected` is the complete old state cell, checked before
any member of the batch is committed. Every record may carry an optional
`evidence` object with `source_id` and an exact unique `quote` for provenance.

## Scope, cognition, and disclosure (2026-10-21)

`property_initial`, `property_observation`, and `event_change` accept three optional
fields:

| Field | Meaning |
| --- | --- |
| `scope` | `world` (default), `belief`, `claim`, or `knowledge` |
| `holder_id` | Person who holds the belief/claim/knowledge; forbidden for `world` |
| `disclosure` | `{source_id, start}`; same disclosure shape as graph assertions |

Semantics:

- `world` records describe the story world and must not have `holder_id`.
- `belief` records describe what a person believes; `claim` records describe what a
  person said (which may be a lie and is not automatically a belief); `knowledge`
  records describe what the system records the person as knowing. None of them
  verifies the proposition against world truth.
- World changes never rewrite belief records. A character can believe `健康` while
  the world says `受伤`; both coexist and are evaluated separately.
- `property_state(..., scope="belief", holder_id="p2")` evaluates the belief
  projection; omitting scope evaluates the world projection.
- `property_history(..., scope=..., holder_id=...)` returns the records for that
  scope; a retraction applies only to its target record.
- `disclosure_cut={source_id, start}` hides records whose disclosure is later than
  the cut. Records without disclosure are `unknown` at a disclosure cut, not
  silently visible. `property_state` returns a `disclosure` object listing
  `hidden` and `unknown` record IDs.

The E0–E3 acceptance case is in `tests/test_property_cognition.py`: world injury,
stale belief, learning event, world recovery; world and belief stay separate, and
a third character with no records is `unrecorded`, not “does not know”. The same
file checks the disclosure cut and `as_of_revision` visibility.

`property_state` hides filtered record IDs by default. Authors/editors can request
`include_diagnostics=True` (CLI `--diagnostics`) to get the `hidden` / `unknown`
record IDs; reader and character views only receive `{cut, restricted}`. A later
record that is hidden at the cut must not change the reader-visible result, and
`as_of_revision` constrains the whole response, not just the final value.

### Dependencies and eligibility

`property_initial`, `property_observation`, and `event_change` may declare
`derived_from` / `support_groups` with typed refs:

```json
{"kind": "assertion", "id": "a17"}
{"kind": "property_record", "id": "p42"}
```

`support_groups` means “all refs in one group, any one group is enough”. No group
means valid unless retracted. Precheck rejects dangling refs and dependency
cycles; cross-family refs must go through `change_precheck`. Unsupported derived
records are excluded from `property_state`, but remain in `property_history` and
in old `as_of_revision` views. “Unsupported” is not a retraction and does not
imply the content is false.

Automatic knowledge propagation is still not implemented: a world change or a
false testimony does not rewrite a character's belief; only records that depend
on an invalid premise lose eligibility.

## Disclosure-only observations

`property_observation` may carry either a story-time `at` or a `disclosure`
position, but not neither:

- `at` present: story-time projection (`property_state`);
- `disclosure` present and `at` absent: disclosure projection
  (`property_disclosure_state`);
- both absent: validation error.

`property_disclosure_state(node_id, property_id, disclosure_cut, scope=..., holder_id=...)`
orders initial/observation records by `source_order` + source offset and applies
only records revealed at or before the cut. It deliberately ignores story time.
The normal `property_state` ignores disclosure-only records, so a fact with no
story-time anchor does not become an initial value or an event change.

The mapper's `disclosure_only=True` mode strips model-provided event anchors and
records facts at their exact evidence position. This is the preferred path for
"record what the reader sees" before story time is established.

## Query and review

`Operations.property_precheck(records, expected_revision)` returns diagnostics.
`property_propose(...)` stores a pending candidate; `property_accept(id,version)`
atomically validates and appends all records or none; `property_reject` retains
the rejected candidate. UI, CLI, and model agents use these Operations methods.
The model may only propose; it cannot accept or physically delete records.

`Operations.property_state(node_id, property_id, at, as_of_revision=None)`
returns a tagged state and supporting record IDs. `at` is a story `TimeRef`
(number, datetime, or event boundary); omitted `at` asks for a current view
which must report unresolved cross-line ordering instead of using insertion
order. `property_history` returns the accepted records and retractions relevant
to that pair. Authoritative records are stored once; query results are rebuilt
projections and may be cached only as invalidatable derived data.
`property_world_state(at, as_of_revision=None)` aggregates those node-local
projections; there is no separately writable world-state table.

The pure core consumes plain graph and property snapshots. It uses the reviewed
event boundary partial order, including numeric coordinates, and never invents
ordering between branches, unrelated timelines, or uncertain intervals. An
incomparable conflicting update yields `conflict` with alternatives and record
IDs. A baseline observed later than a query cannot be moved to world origin.
For `until_changed`, the most recent provably ordered record persists; `instant`
is visible only at its anchor. Reverse relations, such as a person's held items,
are queries over the authoritative item `holder` property, not duplicated writes.

Implementation boundary for parallel work: `property_core.validate_records(
records, graph_snapshot, accepted_records) -> list[dict]` returns diagnostics
with `severity`, `code`, `message`, and optional `object_id`;
`property_core.state_at(graph_snapshot, accepted_records, node_id, property_id,
at=None) -> dict` returns a `state` cell, `record_ids`, and `reason`;
`property_core.history(accepted_records, node_id, property_id) -> list[dict]`.
The storage adapter owns revision filtering and calls these functions; the core
never imports SQLite, Qt, model gateways, or Operations.

The v5 property system does not implement cognitive masks, information flow
policies, or an automatic world-rule engine.
