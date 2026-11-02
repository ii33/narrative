"""Assertions for the actual schema, not removed staging compatibility APIs."""


def assert_no_staging(ops):
    with ops.project.store.connect() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {"candidates", "property_candidates", "change_candidates"} & tables


def action_receipts(ops):
    with ops.project.store.connect() as db:
        return [dict(row) for row in db.execute("SELECT id,revision,summary FROM actions ORDER BY revision")]
