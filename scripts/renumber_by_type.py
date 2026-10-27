"""把已有工程的节点编号改成「每种类型各一套」。

旧编号是全工程一本流水号（1…164）：第七个出现的节点叫 7，不管它是人还是物品。
新规则是每种节点各自一套（P1…P11 / I1…I31 / E1…E81），数字之间互不相关。
编号是主键，所以引用它的地方要一起改：

* 节点自己：`id`、`participants`、`events`、`predecessors`、`successors`、`parent_id`
* 属性记录：`node_id`、`event_id`、`holder_id`、`related_nodes`，以及节点值型属性的 `value`
* 还没审的候选：`graph_objects` / `property_records` / `records` 里同样的字段

`context` / `timeline` / `source_order` / 属性定义 / 记录 id 都不是节点编号，不动。

用法：
    python scripts/renumber_by_type.py PROJECT.sqlite3 [--dry-run]

动手之前会在旁边留一份 `<工程>_before-renumber.sqlite3`。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from narrative.graph_store import GRAPH_SCHEMA_VERSION  # noqa: E402
from narrative.order_ids import order_key, prefix_for  # noqa: E402

# 哪些字段装着节点编号。
NODE_ID_FIELDS = ("node_id", "event_id", "holder_id")
NODE_ID_LISTS = ("participants", "events", "predecessors", "successors", "related_nodes")
NODE_ID_SINGLE = ("id", "parent_id")
TRIGGERS = ("property_records_no_update", "property_records_no_delete")


def _node_valued(records: list[dict]) -> set[str]:
    """value_type 是 node/either 的属性，它的 value 是节点编号而不是普通文字。"""
    return {
        str(item.get("id"))
        for item in records
        if item.get("kind") == "property_definition"
        and item.get("value_type") in {"node", "either"}
    }


def _mapping(nodes: list[dict]) -> dict[str, str]:
    by_type: dict[str, list[tuple[tuple[int, ...], str]]] = {}
    for node in nodes:
        key = order_key(str(node.get("id") or ""))
        if key is None:
            raise SystemExit(f"节点编号不是顺序号：{node.get('id')!r}")
        by_type.setdefault(str(node.get("node_type")), []).append((key, str(node["id"])))
    mapping: dict[str, str] = {}
    for node_type, items in by_type.items():
        prefix = prefix_for(node_type)
        for number, (_key, old) in enumerate(sorted(items), start=1):
            mapping[old] = f"{prefix}{number}"
    return mapping


def _rewrite(payload: dict, mapping: dict[str, str], node_valued: set[str]) -> dict:
    """按 kind 改一份节点/记录；别的 kind 原样返回。"""
    kind = payload.get("kind")
    data = dict(payload)
    if kind == "node":
        for field in NODE_ID_SINGLE:
            if field in data and isinstance(data[field], str):
                data[field] = mapping.get(data[field], data[field])
        for field in NODE_ID_LISTS:
            if isinstance(data.get(field), list):
                data[field] = [mapping.get(item, item) for item in data[field]]
    elif kind == "property_value":
        for field in NODE_ID_FIELDS:
            if isinstance(data.get(field), str):
                data[field] = mapping.get(data[field], data[field])
        if isinstance(data.get("related_nodes"), list):
            data["related_nodes"] = [
                mapping.get(item, item) for item in data["related_nodes"]
            ]
        # 只有节点值型属性的取值才是编号；`height = "180"` 这种不能碰。
        if str(data.get("property_id")) in node_valued and isinstance(data.get("value"), str):
            data["value"] = mapping.get(data["value"], data["value"])
    elif kind == "property_retraction":
        if isinstance(data.get("event_id"), str):
            data["event_id"] = mapping.get(data["event_id"], data["event_id"])
    return data


def _walk(value, mapping: dict[str, str], node_valued: set[str]):
    """候选里的对象是嵌套的：顺着 dict/list 一路改下去。"""
    if isinstance(value, list):
        return [_walk(item, mapping, node_valued) for item in value]
    if isinstance(value, dict):
        if "kind" in value:
            return _rewrite(value, mapping, node_valued)
        return {key: _walk(item, mapping, node_valued) for key, item in value.items()}
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只打印对照表，不改文件")
    args = parser.parse_args()
    project = args.project.resolve()
    if not project.is_file():
        parser.error("工程文件不存在")

    with closing(sqlite3.connect(project)) as db:
        db.row_factory = sqlite3.Row
        nodes = [
            json.loads(row["data"])
            for row in db.execute("select data from objects where kind='node'")
        ]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if any(str(node.get("id", ""))[:1].isalpha() for node in nodes):
            # 编号已经是按类型的，只差把文件版本标上来。
            if version != GRAPH_SCHEMA_VERSION:
                db.execute(f"PRAGMA user_version={GRAPH_SCHEMA_VERSION}")
                db.commit()
                print(f"编号已经是按类型的，版本从 v{version} 升到 v{GRAPH_SCHEMA_VERSION}。")
            else:
                print("这个工程已经是按类型编号了，不用再改。")
            return 0
        mapping = _mapping(nodes)
        records = [
            json.loads(row["data"])
            for row in db.execute("select data from property_records")
        ]
        node_valued = _node_valued(records)

        print(f"节点 {len(nodes)} 个，改成 {len(set(mapping.values()))} 个编号")
        by_type: dict[str, list[str]] = {}
        for old, new in mapping.items():
            by_type.setdefault(new[0], []).append(f"{old}→{new}")
        for prefix in sorted(by_type):
            pairs = sorted(by_type[prefix], key=lambda text: order_key(text.split("→")[0]) or ())
            print(f"  {prefix}: " + "  ".join(pairs))
        if args.dry_run:
            return 0

        # 先落盘：只拷 .sqlite3 而不合并 WAL 会漏掉最近的提交。
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        backup = project.with_name(project.stem + "_before-renumber.sqlite3")
        shutil.copyfile(project, backup)
        print(f"备份：{backup}")

        # 属性记录是 append-only：先把触发器摘掉，改完再装回去。
        trigger_sql = {
            row["name"]: row["sql"]
            for row in db.execute(
                "select name, sql from sqlite_master where type='trigger' and name in (?, ?)",
                TRIGGERS,
            )
        }
        for name in trigger_sql:
            db.execute(f"drop trigger if exists {name}")

        changed_objects = 0
        for row in db.execute("select id, kind, data from objects where kind='node'"):
            data = _rewrite(json.loads(row["data"]), mapping, node_valued)
            db.execute(
                "update objects set id=?, data=? where id=?",
                (data["id"], json.dumps(data, ensure_ascii=False), row["id"]),
            )
            changed_objects += 1

        changed_records = 0
        for row in db.execute("select id, data from property_records"):
            data = _rewrite(json.loads(row["data"]), mapping, node_valued)
            db.execute(
                "update property_records set data=? where id=?",
                (json.dumps(data, ensure_ascii=False), row["id"]),
            )
            changed_records += 1

        changed_candidates = 0
        for table in ("change_candidates", "property_candidates", "candidates"):
            for row in db.execute(f"select id, data from {table}"):
                data = _walk(json.loads(row["data"]), mapping, node_valued)
                db.execute(
                    f"update {table} set data=? where id=?",
                    (json.dumps(data, ensure_ascii=False), row["id"]),
                )
                changed_candidates += 1

        for sql in trigger_sql.values():
            db.execute(sql)
        # 编号的含义变了，文件版本也跟着变：旧库不再当成同一个格式打开。
        db.execute(f"PRAGMA user_version={GRAPH_SCHEMA_VERSION}")
        db.commit()

        # 资料是按编号存的（`<工程>_material/<编号>.json` 和同名目录），
        # 编号变了它们就成了孤儿，一起改名。
        material = project.with_name(project.stem + "_material")
        moved = 0
        if material.is_dir():
            for old, new in mapping.items():
                for source, target in (
                    (material / f"{old}.json", material / f"{new}.json"),
                    (material / old, material / new),
                ):
                    if source.exists() and not target.exists():
                        source.rename(target)
                        moved += 1

        print(
            f"改完：节点 {changed_objects}、属性记录 {changed_records}、"
            f"候选 {changed_candidates}、资料 {moved}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
