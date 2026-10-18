from importlib.resources import files

from .models import Assertion, ChangeSet, Entity
from .service import Service


def seed(service: Service) -> str:
    if service.store.rows("sources"):
        raise ValueError("演示资料仅导入空项目，请新建项目或切换演示数据库")
    chapter = files("narrative").joinpath("sample.txt")
    source_id = service.import_text("雨夜的钥匙 · 自编演示", chapter.read_bytes().decode("utf-8"))
    lin, shen, key = (
        Entity(name="林舟"),
        Entity(name="沈砚", aliases=["阿砚"]),
        Entity(name="铜钥匙", entity_type="item"),
    )
    change = ChangeSet(
        expected_revision=service.store.revision,
        operations=[lin, shen, key],
        note="演示：手工登记实体",
    )
    service.propose(change)
    service.accept(change.id, 1)
    quote = "沈砚把钥匙藏进左袖，却对林舟说：“钥匙已经丢了。”"
    assertion = Assertion(
        subject_id=key.id,
        predicate="状态",
        value="已丢失",
        scope="claim",
        holder_id=shen.id,
        evidence=service.locate(source_id, quote),
        certainty="unknown",
    )
    service.propose(
        ChangeSet(
            expected_revision=service.store.revision,
            operations=[assertion],
            note="待核验示例：沈砚的说法不等于钥匙真实丢失",
        )
    )
    return source_id
