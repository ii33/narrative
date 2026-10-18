import hashlib
from typing import Literal

from .models import OBJECT_TABLES, ChangeSet, Diagnostic
from .records_checks import validate_records
from .state import diagnose


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check(
    change: ChangeSet,
    revision: int,
    entities: dict,
    sources: dict,
    events: dict | None = None,
    records: dict | None = None,
    assertions: dict | None = None,
) -> list[Diagnostic]:
    results: list[Diagnostic] = []

    def issue(
        rule,
        obj,
        message,
        coverage,
        severity: Literal["error", "warning", "info"] = "error",
        verdict: Literal["consistent", "conflict", "insufficient", "unsupported"] = "conflict",
    ):
        results.append(
            Diagnostic(
                rule_id=rule,
                object_id=obj,
                message=message,
                coverage=coverage,
                severity=severity,
                verdict=verdict,
            )
        )

    if change.expected_revision != revision:
        issue("REV", change.id, "候选基于旧修订，请查看差异后重新核验", "全局修订")
    ids = [op.id for op in change.operations]
    if len(set(ids)) != len(ids):
        issue("REF", change.id, "同一候选重复修改同一对象", "操作 ID")
    current = {}
    for collection in (entities, assertions or {}, events or {}, records or {}):
        for object_id, obj in collection.items():
            if object_id in current:
                issue("REF", object_id, "对象 ID 不能跨类型复用", "全库对象身份")
            current[object_id] = obj
    final = current | {op.id: op.model_dump() for op in change.operations}
    for op in change.operations:
        saved = current.get(op.id)
        if saved and saved["kind"] != op.kind:
            issue("REF", op.id, "对象 ID 不能跨类型复用", "全库对象身份")
        if (
            saved
            and saved.get("locked")
            and saved["kind"] == op.kind
            and type(op).model_validate(saved).model_dump() != op.model_dump()
            and not change.allow_locked_changes
        ):
            issue("LOCKED", op.id, "对象已锁定；明确允许锁定修改后才可纠正", "锁定对象保护")
        if op.kind == "entity":
            if not op.name.strip():
                issue("REF", op.id, "姓名不能为空白", "实体名称")
            if any(
                entity["id"] != op.id
                and set([op.name, *op.aliases]) & set([entity["name"], *entity.get("aliases", [])])
                for entity in final.values()
                if entity["kind"] == "entity"
            ):
                issue(
                    "IDENTITY",
                    op.id,
                    "姓名或别名与已有实体重叠，请人工对齐身份",
                    "名称与别名",
                    "warning",
                    "insufficient",
                )
        else:
            if op.status != "accepted":
                issue(
                    "STATE_DISPUTED",
                    op.id,
                    "有争议或已取代记录保留证据，不用于确定状态",
                    "记录状态",
                    "warning",
                    "insufficient",
                )
        if op.kind == "event":
            if not op.title.strip():
                issue("REF", op.id, "事件标题不能为空白", "事件标题")
            if op.time_kind == "unknown":
                issue(
                    "TIME",
                    op.id,
                    "事件时间未定，不自动推断先后",
                    "事件故事时间",
                    "info",
                    "insufficient",
                )
        elif op.kind == "assertion":
            if op.scope == "knowledge" and not op.learning_method:
                issue(
                    "KNOW",
                    op.id,
                    "未记录知识获得方式，不能证明人物如何得知",
                    "来源字段",
                    "warning",
                    "insufficient",
                )
            if op.story_time is None:
                issue("TIME", op.id, "故事时间未解析", "故事有效时间", "info", "insufficient")

    source_hashes: dict[str, str] = {}
    for obj in final.values():
        if (
            obj["kind"] != "entity"
            and obj.get("origin", "original") == "original"
            and not obj.get("evidence")
        ):
            issue("EVID", obj["id"], "原著记录必须有可定位的证据", "全库证据存在性")
        for ev in obj.get("evidence", []):
            source = sources.get(ev["source_id"])
            if source and ev["source_id"] not in source_hashes:
                source_hashes[ev["source_id"]] = digest(source["text"])
            if (
                not source
                or source["hash"] != ev["source_hash"]
                or source_hashes.get(ev["source_id"]) != ev["source_hash"]
                or not 0 <= ev["start"] < ev["end"] <= len(source["text"])
                or source["text"][ev["start"] : ev["end"]] != ev["quote"]
            ):
                issue(
                    "EVID",
                    obj["id"],
                    "证据原文版本、字符范围或原句不匹配",
                    "全库精确字符跨度与 SHA-256",
                )
    results.extend(validate_records(final, set(ids), revision))
    if not any(d.rule_id == "REF" and d.severity == "error" for d in results):
        collections: dict[str, list[dict]] = {table: [] for table in set(OBJECT_TABLES.values())}
        for obj in final.values():
            collections[OBJECT_TABLES[obj["kind"]]].append(obj)
        results.extend(
            Diagnostic.model_validate(d) for d in diagnose({"revision": revision, **collections})
        )
    # Disputed records retain contradictory interpretations. Broken references,
    # forged evidence and provenance cycles remain unsavable.
    for diagnostic in results:
        obj = final.get(diagnostic.object_id)
        if (
            obj
            and obj.get("status", "accepted") != "accepted"
            and diagnostic.severity == "error"
            and diagnostic.rule_id not in {"REF", "EVID", "LOCKED", "REV"}
        ):
            diagnostic.severity = "warning"
            diagnostic.verdict = "insufficient"
    results.append(
        Diagnostic(
            rule_id="SEMANTIC",
            severity="info",
            verdict="unsupported",
            message="未自动核验引文是否支持记录、说谎或文学合理性；需人工审核",
            coverage="全库类型、引用、证据、修订、锁定与受支持的事件时间／状态规则",
        )
    )
    return results
