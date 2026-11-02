"""Read-only assertions for current-format engineering QA scripts."""
import json


def read_actions(ops):
    with ops.project.store.connect() as db:
        return [dict(row) | {"summary": json.loads(row["summary"])}
                for row in db.execute("SELECT id,revision,action,summary FROM actions ORDER BY revision")]


def staging_tables_absent(ops):
    with ops.project.store.connect() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return not tables.intersection({"candidates", "property_candidates", "change_candidates"})
