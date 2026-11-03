"""One bounded real task: inspect old screenplay storage, reset, then create.

The malformed storage is a deterministic isolated fixture, never a user project.
No retries, migrations, backups or post-task repairs are performed.
"""
from __future__ import annotations

import sqlite3

import accept_screenplay as qa

PROMPT_VERSION = "screenplay-storage-reset-acceptance/1"
TARGET_TITLE = "重建后的验收剧本"
_BASE_SEED = qa.seed
TASK = """这是独立测试工程中的一次真实最小验收。现有剧本是可丢弃的合成旧结构空壳；用户明确授权丢弃本工程全部剧本业务数据，无需兼容、迁移或备份。不能修改原文、提取、改编、模型配置或别的工程。
请在本次同一任务中实际调用工具完成，不只建议：
1. inspect_screenplay_storage只读检查，确认旧结构不完整、缺script_timelines、存在非空剧本域数据；读取row_counts与expected_storage_fingerprint。
2. reset_screenplay_storage(reason=明确解释此验收授权,scope='screenplay',expected_storage_fingerprint=刚读值,confirm_discard=true)原子丢弃旧剧本域并建当前11表。若工具拒绝，不得绕过或直接SQL，不另开任务重试。
3. create_screenplay(title='重建后的验收剧本',format='series',language='zh-CN',reason=验收新建)创建新剧本。
4. inspect_screenplay_storage确认ready且不缺表；read_screenplay读取刚创建真实ID，read_screenplay_timeline读取其独立空时间线；check_screenplay确认结构有效。不要导入时间点或生成场表、正文。
5. 简述已检查的旧行数、reset结果、新剧本ID与独立时间线ID。非法工具参数可在本任务按诊断更正，但不重新启动任务或请求重试。
"""


def seed(ops):
    original = _BASE_SEED(ops)
    report = ops.inspect_screenplay_storage()
    ops.reset_screenplay_storage("准备可丢弃旧空壳的确定性验收fixture", "screenplay",
        report["expected_storage_fingerprint"], True)
    shell = ops.create_screenplay("可丢弃的旧结构空壳", format="series", reason="确定性旧空壳fixture")["screenplay"]
    with ops._store.connect(write=True) as db:
        db.execute("DROP TABLE script_timelines")
    storage = ops.inspect_screenplay_storage()
    assert storage["status"] == "incomplete" and storage["row_counts"]["screenplays"] == 1
    assert storage["row_counts"]["script_episodes"] == 1 and storage["row_counts"]["script_units"] == 0
    return {"source_id": original["source_id"], "screenplay_id": shell["id"], "old_screenplay_id": shell["id"],
        "storage_before": storage, "outside_before": outside(ops), "target_title": TARGET_TITLE}


def outside(ops):
    with ops._store.connect() as db:
        return {table: [list(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
                       if table != "meta" or not str(row[0]).startswith("readable_id:screenplay:")]
            for table in ("objects", "sources", "property_records", "meta", "provider_settings")}


def resolve_fixture(ops, fixture):
    matches = [row for row in ops.list_screenplays() if row["title"] == TARGET_TITLE and row["id"] != fixture["old_screenplay_id"]]
    if len(matches) != 1:
        raise ValueError("真实任务未恰好创建一个目标剧本；不人工修补任务结果")
    return fixture | {"screenplay_id": matches[0]["id"]}


def validate(ops, fixture, screenplay, calls, report):
    storage = ops.inspect_screenplay_storage()
    local = ops.read_screenplay_timeline(fixture["screenplay_id"])
    with ops._store.connect() as db:
        db.row_factory = sqlite3.Row
        resets = [dict(row) for row in db.execute("SELECT * FROM actions WHERE action='screenplay_storage_reset' ORDER BY rowid")]
    evidence = {"storage_before": fixture["storage_before"], "storage_after": storage,
        "outside_meta_excludes_only": ["readable_id:screenplay:"],
        "reset_actions": resets, "timeline": local, "final_screenplay": screenplay}
    # The generic QA runner saves report after this callback, including evidence.
    report["storage_evidence"] = evidence
    return {"storage_ready": storage["status"] == "ready" and not storage["missing_tables"],
        "old_nonempty_fixture": fixture["storage_before"]["total_rows"] > 0,
        "only_new_screenplay": len(ops.list_screenplays()) == 1 and screenplay["screenplay"]["title"] == TARGET_TITLE,
        "old_identity_removed": screenplay["screenplay"]["id"] != fixture["old_screenplay_id"],
        "series_episode_created": screenplay["screenplay"]["format"] == "series" and len(screenplay["episodes"]) == 1,
        "independent_empty_timeline": local["timeline_id"] == screenplay["screenplay"]["timeline_id"] and not local["nodes"] and not local["properties"],
        "no_segments_or_body_generated": screenplay["segments"] == [],
        "outside_domain_unchanged": outside(ops) == fixture["outside_before"],
        "model_reset_audited": len(resets) == 2,
        "screenplay_static_ok": ops.check_screenplay(fixture["screenplay_id"])["ok"]}


def main():
    from narrative import tool_api
    required = ["inspect_screenplay_storage", "reset_screenplay_storage", "create_screenplay",
                "read_screenplay", "read_screenplay_timeline", "check_screenplay"]
    if any(name not in tool_api.TOOLS for name in required):
        raise RuntimeError("Storage tools are not registered; no model request started")
    qa.seed = seed
    qa.TASK = TASK
    qa.PROMPT_VERSION = PROMPT_VERSION
    qa.FIXTURE_ORIGIN = "deterministic Operations source/adaptation; isolated deliberately incomplete old ten-table empty-shell screenplay storage"
    return qa.main(validate=validate, required_tools=required, use_writing_target=False,
                   prompt_factory=lambda fixture: TASK + "\n本工程旧空壳ID=" + fixture["old_screenplay_id"],
                   fixture_resolver=resolve_fixture)


if __name__ == "__main__":
    raise SystemExit(main())
