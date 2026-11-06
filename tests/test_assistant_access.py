"""Chat configuration stays separate from captured task capabilities and graph data."""
import pytest
from test_screenplay_writing import project as project

from narrative.chat_store import ChatStore
from narrative.operations import Operations
from narrative.project_package import layout


def test_chat_access_defaults_persists_and_is_isolated(project):
    ops=project[0]
    store=ChatStore(ops.project_info().path)
    assert ops.read_assistant_access()=={"chat_id":None,"access_mode":"standard","access_revision":0}
    first=store.create("A")
    second=store.create("B")
    store.append(first["id"],"user","保留现有对话")
    before=store.load(first["id"])
    graph=ops.graph_snapshot()
    result=ops.set_assistant_access(first["id"],"full")
    assert result=={"chat_id":first["id"],"access_mode":"full","access_revision":1}
    assert ops.read_assistant_access(second["id"])["access_mode"]=="standard"
    after=store.load(first["id"])
    assert after["session"]==before["session"] and after["messages"]==before["messages"]
    assert ops.graph_snapshot()==graph
    reopened=Operations.open(layout(ops.project_info().path).entry)
    assert reopened.read_assistant_access(first["id"])==result
    assert reopened.set_assistant_access(first["id"],"standard")["access_revision"]==2
    for value in ("unrestricted","",None,{},True):
        with pytest.raises(ValueError,match="访问模式"):
            reopened.set_assistant_access(first["id"],value)
    assert reopened.read_assistant_access(first["id"])["access_mode"]=="standard"
