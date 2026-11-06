"""One revision-local batch: validate once, commit only immutable encoded deltas."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from .graph_store import GraphStore


@dataclass(frozen=True)
class BatchSnapshot:
    store: GraphStore = field(repr=False)
    _graph: dict = field(repr=False)
    _records: list[dict] = field(repr=False)
    _sources: dict = field(repr=False)
    _meta: dict = field(repr=False)
    _cursor: list[int] = field(repr=False)

    @property
    def revision(self) -> int:
        return self._graph['revision']

    @property
    def reader(self):
        return BatchReader(self)


class BatchReader:
    def __init__(self, batch: BatchSnapshot):
        self.batch = batch

    def new_property_id(self) -> str:
        identifier = f'PC{self.batch._cursor[0]}'
        self.batch._cursor[0] += 1
        return identifier

    def graph_snapshot(self) -> dict:
        return deepcopy(self.batch._graph)

    def property_records(self) -> list[dict]:
        return deepcopy(self.batch._records)

    def source(self, identifier: str) -> dict:
        if identifier not in self.batch._sources:
            raise ValueError('原文不存在')
        return dict(self.batch._sources[identifier])

    def sources(self) -> list[dict]:
        return [dict(row) for row in self.batch._graph['sources']]

    def graph_project_info(self) -> dict:
        return dict(self.batch._meta, revision=self.batch.revision,
                    source_count=len(self.batch._sources), node_count=len(self.batch._graph['nodes']))


@dataclass(frozen=True)
class PreparedChanges:
    _snapshot: BatchSnapshot = field(repr=False)
    _graph: dict = field(repr=False)
    _records: list[dict] = field(repr=False)
    _diagnostics: tuple[dict, ...] = field(repr=False)
    _object_rows: tuple[tuple[str, str, str], ...] = field(repr=False)
    _record_rows: tuple[tuple[str, str, str], ...] = field(repr=False)
    _delete_objects: tuple[str, ...] = field(repr=False)
    _delete_records: tuple[str, ...] = field(repr=False)
    _next_property_id: int = field(repr=False)

    @property
    def graph(self) -> dict:
        return deepcopy(self._graph)

    @property
    def records(self) -> list[dict]:
        return deepcopy(self._records)

    @property
    def diagnostics(self) -> list[dict]:
        return deepcopy(list(self._diagnostics))

    @property
    def valid(self) -> bool:
        return not any(row.get('severity') == 'error' for row in self._diagnostics)

    @property
    def counts(self) -> dict:
        return {'graph_count': len(self._object_rows), 'property_count': len(self._record_rows),
                'deleted_graph_count': len(self._delete_objects), 'deleted_property_count': len(self._delete_records)}

    def preview(self) -> dict:
        revision = self._snapshot.revision
        return {'valid': self.valid, 'revision': revision, 'expected_revision': revision,
                'diagnostics': self.diagnostics, **self.counts}


def capture(store) -> BatchSnapshot:
    graph = store.snapshot(validation_data=True)
    data = graph.pop('_validation')
    return BatchSnapshot(store, graph, data['records'], data['sources'], data['meta'],
                         [int(data['meta'].get('next_property_id', '1'))])


def validate(snapshot: BatchSnapshot, graph: dict, records: list[dict]) -> list[dict]:
    from .temporal_state import diagnostics
    return diagnostics(snapshot.store, graph, records, sources=snapshot._sources)


def _state(snapshot: BatchSnapshot, graph: dict, records: list[dict], issues: list[dict]) -> PreparedChanges:
    previous_objects = {row['id']: row for row in snapshot._graph['objects']}
    previous_records = {row['id']: row for row in snapshot._records}
    objects = {row['id']: row for row in graph['objects']}
    values = {row['id']: row for row in records}
    # Compare decoded objects outside the write transaction; encode changed rows only.
    def delta(current, previous):
        return tuple((row['id'], row['kind'], json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False))
                     for identifier, row in current.items() if previous.get(identifier) != row)
    object_rows, record_rows = delta(objects, previous_objects), delta(values, previous_records)
    next_id = int(snapshot._meta.get('next_property_id', '1'))
    for identifier, kind, _payload in record_rows:
        if kind == 'property_change' and identifier not in previous_records and identifier.startswith('PC') and identifier[2:].isdigit():
            next_id = max(next_id, int(identifier[2:]) + 1)
    return PreparedChanges(snapshot, graph, records, tuple(issues), object_rows, record_rows,
                           tuple(previous_objects.keys() - objects.keys()),
                           tuple(previous_records.keys() - values.keys()), next_id)



def prepare(snapshot: BatchSnapshot, graph_objects: list[dict], property_records: list[dict],
            expected_revision: int) -> PreparedChanges:
    from .graph_service import GraphService
    from .temporal_state import prepare as prepare_values

    if type(expected_revision) is not int or expected_revision != snapshot.revision:
        return _state(snapshot, snapshot._graph, snapshot._records,
                      [{'severity': 'error', 'code': 'REVISION', 'message': '正式资料版本已变化，请刷新'}])
    if not isinstance(property_records, list) or any(not isinstance(row, dict) for row in property_records):
        return _state(snapshot, snapshot._graph, snapshot._records,
                      [{'severity': 'error', 'code': 'FORMAT', 'message': '属性修改必须为对象数组'}])
    assigned = []
    reserved = {row["id"] for row in snapshot._records} | {row["id"] for row in property_records if isinstance(row.get("id"), str)}
    allocator = snapshot.reader
    for row in property_records:
        if row.get('kind') == 'property_change' and 'id' not in row:
            identifier = allocator.new_property_id()
            while identifier in reserved:
                identifier = allocator.new_property_id()
            reserved.add(identifier)
            row = row | {'id': identifier}
        assigned.append(row)
    objects, issues = GraphService(snapshot.store).normalize(graph_objects)
    if issues:
        return _state(snapshot, snapshot._graph, snapshot._records, issues)
    try:
        graph, records = prepare_values(snapshot._graph, snapshot._records, objects, assigned)
    except (ValueError, TypeError) as exc:
        return _state(snapshot, snapshot._graph, snapshot._records,
                      [{'severity': 'error', 'code': 'RECORD', 'message': str(exc)}])
    return _state(snapshot, graph, records, validate(snapshot, graph, records))


def prepare_state(snapshot: BatchSnapshot, graph: dict, records: list[dict]) -> PreparedChanges:
    return _state(snapshot, graph, records, validate(snapshot, graph, records))


def _write_delta(db, prepared: PreparedChanges, revision: int) -> None:
    if prepared._delete_objects:
        db.executemany('DELETE FROM objects WHERE id=?', ((identifier,) for identifier in prepared._delete_objects))
    if prepared._delete_records:
        db.executemany('DELETE FROM property_records WHERE id=?', ((identifier,) for identifier in prepared._delete_records))
    if prepared._object_rows:
        db.executemany('INSERT INTO objects(id,kind,data,accepted_revision) VALUES (?,?,?,?) '
                       'ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,data=excluded.data,accepted_revision=excluded.accepted_revision',
                       ((*row, revision) for row in prepared._object_rows))
    if prepared._record_rows:
        db.executemany('INSERT INTO property_records(id,kind,data,accepted_revision) VALUES (?,?,?,?) '
                       'ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,data=excluded.data,accepted_revision=excluded.accepted_revision',
                       ((*row, revision) for row in prepared._record_rows))


def _committed_changes(prepared):
    before_objects = {row['id']: row for row in prepared._snapshot._graph['objects']}
    before_records = {row['id']: row for row in prepared._snapshot._records}
    changes = []
    def compact(row, category):
        kind = row.get('node_type') or row.get('kind', 'object')
        return {'domain': 'extraction', 'action': category, 'object_type': kind, 'id': row['id'],
                'name': row.get('name') or row.get('title') or row.get('property_id') or row['id'], 'count': 1}
    for encoded, prior in ((prepared._object_rows, before_objects), (prepared._record_rows, before_records)):
        for identifier, _kind, data in encoded:
            row = json.loads(data)
            category = 'updated' if identifier in prior else 'created'
            changes.append(compact(row, category))
    for identifiers, prior in ((prepared._delete_objects, before_objects), (prepared._delete_records, before_records)):
        for identifier in identifiers:
            changes.append(compact(prior.get(identifier, {'id': identifier}), 'deleted'))
    return changes


def commit(store, prepared: PreparedChanges, reason: str, *, action: str = 'apply', transaction_callback=None) -> dict:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('请说明修改原因')
    if Path(store.path) != Path(prepared._snapshot.store.path):
        raise ValueError('预检结果不属于当前工程')
    if not prepared.valid:
        return prepared.preview() | {'status': 'rejected'}
    changed = sum(prepared.counts.values())
    committed_changes = _committed_changes(prepared) if changed else []
    with store.connect(write=True) as db:
        current = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        if current != prepared._snapshot.revision:
            raise ValueError('正式资料版本已变化，预检不能复用于新版本')
        revision = current + 1 if changed else current
        if changed:
            _write_delta(db, prepared, revision)
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision),))
            if prepared._next_property_id != int(prepared._snapshot._meta.get('next_property_id', '1')):
                db.execute("UPDATE meta SET value=? WHERE key='next_property_id'",
                           (str(prepared._next_property_id),))
            summary = {'object_ids': [row[0] for row in prepared._object_rows],
                       'record_ids': [row[0] for row in prepared._record_rows],
                       'deleted_object_ids': list(prepared._delete_objects),
                       'deleted_record_ids': list(prepared._delete_records), 'changed_count': changed,
                       'committed_changes': committed_changes}
            db.execute('INSERT INTO actions(id,revision,action,reason,summary) VALUES (?,?,?,?,?)',
                       (f'r{revision}', revision, action, reason,
                        json.dumps(summary, ensure_ascii=False)))
        result = {'status': 'accepted' if changed else 'unchanged', 'revision': revision, **prepared.counts, 'committed_changes': committed_changes}
        if transaction_callback is not None:
            transaction_callback(db, result)
    return result
