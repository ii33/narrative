"""SDK output and HTTP tool feedback must not overwrite each other."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from narrative.chat_store import ChatStore
from narrative.operations import Operations


def test_two_store_instances_preserve_concurrent_appends_and_annotations(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "messages.nproj")
    first = ChatStore(ops.project_info().path)
    second = ChatStore(ops.project_info().path)
    chat = first.create("合成并发消息")
    first.append(chat["id"], "assistant", "共享标记", message_id="anchor")
    save = ChatStore._save

    def delayed_save(self, data):
        time.sleep(0.001)  # Widen the lost-update window without synchronizing inside the lock.
        return save(self, data)

    monkeypatch.setattr(ChatStore, "_save", delayed_save)
    barrier = threading.Barrier(2)

    def append(store, prefix, role):
        barrier.wait(timeout=3)
        for number in range(30):
            store.append(chat["id"], role, f"{prefix}-{number}", message_id=f"{prefix}-{number}")
            store.annotate_message(chat["id"], "anchor", **{prefix: number})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(append, first, "model", "assistant"),
                   pool.submit(append, second, "feedback", "system")]
        for future in futures:
            future.result(timeout=10)
    messages = first.load(chat["id"])["messages"]
    assert len(messages) == 61
    assert {message["message_id"] for message in messages} == {
        "anchor", *(f"model-{number}" for number in range(30)),
        *(f"feedback-{number}" for number in range(30)),
    }
    assert messages[0]["model"] == messages[0]["feedback"] == 29


def test_pointer_and_chat_readers_serialize_with_atomic_writes_across_instances(tmp_path, monkeypatch):
    """Use real open handles: Windows forbids replacing one while it is read."""
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path / "providers.sqlite3"))
    ops = Operations.create_graph_project(tmp_path / "readers.nproj")
    first = ChatStore(ops.project_info().path)
    second = ChatStore(ops.project_info().path)
    chats = [first.create("甲会话"), first.create("乙会话")]
    ids = [chat["id"] for chat in chats]
    first.append(ids[0], "assistant", "标记", message_id="anchor")
    read_text = type(first.root).read_text

    def held_read(path, *args, **kwargs):
        if path.parent == first.root and (path.name == ".active" or path.suffix == ".json"):
            # Retain a genuine file handle across a scheduler yield; this is the
            # same sharing constraint encountered by the live UI reader.
            with path.open("r", *args, **kwargs) as stream:
                time.sleep(0.001)
                return stream.read()
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(first.root), "read_text", held_read)
    barrier = threading.Barrier(4)

    def switch():
        barrier.wait(timeout=3)
        for index in range(50):
            second.set_active(ids[index % 2])

    def write_messages():
        barrier.wait(timeout=3)
        for index in range(50):
            first.append(ids[0], "assistant", str(index), message_id=f"m-{index}")
            first.annotate_message(ids[0], "anchor", progress=index)
            first.rename(ids[0], f"消息{index}")

    def read_messages():
        barrier.wait(timeout=3)
        for _ in range(50):
            assert second.active()["id"] in ids
            assert {row["id"] for row in second.list()} == set(ids)
            assert first.load(ids[0])["messages"][0]["message_id"] == "anchor"

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(switch), pool.submit(write_messages),
                   pool.submit(read_messages), pool.submit(read_messages)]
        for future in futures:
            future.result(timeout=15)
    assert len(first.load(ids[0])["messages"]) == 51
    assert second.load(ids[0])["messages"][0]["progress"] == 49
    second.delete(second.active()["id"])
    assert first.active()["id"] in {row["id"] for row in first.list()}
