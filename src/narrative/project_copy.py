"""Independent full-project copies, published only after a locked snapshot."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack, closing
from pathlib import Path
from uuid import uuid4

from .leases import ProjectLease
from .project_package import Layout, layout, write_project
from .provider_store import ProviderStore

_SQLITE_HEADER = b"SQLite format 3\x00"
_TEMP = re.compile(r".*[.-][0-9a-f]{32}\.tmp\Z")


def _within(path, parent):
    return path == parent or parent in path.parents


def _overlap(left, right):
    return _within(left, right) or _within(right, left)


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite(path):
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(16) == _SQLITE_HEADER


def _roots(found):
    return {"data": found.root, "material": found.material, "extraction": found.extraction, "chats": found.chats}


def _skip(path, source, provider_path):
    if path == source.entry or path == provider_path:
        return True
    if path.name in {"tool-fingerprint-refs.sqlite3", "tool-fingerprint-refs.sqlite3-wal", "tool-fingerprint-refs.sqlite3-shm", "tool-fingerprint-refs.sqlite3-journal"} and path.parent == source.chats:
        return True
    locks = {source.material / ".write.lock", source.extraction / "assistant" / "task.lock",
             source.extraction / "assistant" / "runtime-owner.lock", source.extraction / "assistant" / "business-actions.lock"}
    if path in locks or _TEMP.fullmatch(path.name):
        return True
    for suffix in ("-wal", "-shm", "-journal"):
        if path.name.endswith(suffix):
            base = path.with_name(path.name[:-len(suffix)])
            if base == provider_path or _sqlite(base):
                return True
    return False


def _inventory(source, provider_path):
    roots = _roots(source)
    result = {}
    for component, root in roots.items():
        if not root.exists():
            continue
        if not root.is_dir() or root.is_symlink():
            raise ValueError("工程附属目录不是独立可复制目录：" + str(root))
        for folder, dirs, files in os.walk(root, followlinks=False):
            directory = Path(folder)
            for name in list(dirs):
                path = directory / name
                if path.is_symlink() or path.is_junction():
                    raise ValueError("工程内含符号链接或目录联接，无法保证副本独立：" + str(path))
                # Nested declared components are copied to their own canonical
                # destination rather than being duplicated under old path names.
                if any(path == other for key, other in roots.items() if key != component and other != root):
                    dirs.remove(name)
            for name in files:
                path = directory / name
                if path.is_symlink():
                    raise ValueError("工程内含文件符号链接，无法保证副本独立：" + str(path))
                if _skip(path, source, provider_path):
                    continue
                result[component, path.relative_to(root).as_posix()] = path
    return result


def _lock_database(stack, path):
    db = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=0.1, isolation_level=None)
    stack.callback(db.close)
    try:
        db.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as exc:
        raise ValueError("源工程数据库正被写入，未创建半成品副本：" + str(path)) from exc
    stack.callback(db.rollback)


def _backup_sqlite(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as copied:
            original.backup(copied)
            copied.commit()
            if copied.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite副本完整性检查失败")


def _copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = source.stat()
    with source.open("rb") as original, destination.open("xb") as copied:
        shutil.copyfileobj(original, copied, 1024 * 1024)
    if (before.st_size, before.st_mtime_ns) != (source.stat().st_size, source.stat().st_mtime_ns):
        raise ValueError("复制期间源附件发生变化，请在写入结束后再创建副本")
    shutil.copystat(source, destination)
    return _hash(destination)


def _mapped(path, source, destination):
    pairs = [(source.material, destination.material), (source.extraction, destination.extraction),
             (source.chats, destination.chats), (source.root, destination.root)]
    pairs.sort(key=lambda pair: len(pair[0].parts), reverse=True)
    for before, after in pairs:
        if _within(path, before):
            return after / path.relative_to(before)
    return None


def _update_current_identity(staged, source, destination, project_id):
    changes, external = [], []
    with closing(sqlite3.connect(staged.db)) as db:
        source_project_id = db.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()[0]
        db.execute("UPDATE meta SET value=? WHERE key='project_id'", (project_id,))
        db.execute("UPDATE meta SET value=? WHERE key='title'", (destination.entry.stem,))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='script_writing_bases' COLLATE NOCASE AND type='table'").fetchone():
            for identifier, raw in list(db.execute("SELECT id,data FROM script_writing_bases")):
                row = json.loads(raw)
                reference = row.get("manifest_ref")
                if not reference:
                    continue
                path = Path(reference)
                mapped = _mapped(path, source, destination) if path.is_absolute() else None
                if mapped is None:
                    external.append({"basis_id": identifier, "manifest_ref": reference})
                    continue
                row["manifest_ref"] = str(mapped)
                db.execute("UPDATE script_writing_bases SET data=? WHERE id=?", (json.dumps(row, ensure_ascii=False), identifier))
                changes.append(identifier)
        revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        summary = {"source_entry": str(source.entry), "source_project_id": source_project_id,
                   "destination_entry": str(destination.entry), "project_id": project_id, "title": destination.entry.stem}
        db.execute("INSERT INTO actions(id,revision,action,reason,summary) VALUES(?,?,?,?,?)",
                   (uuid4().hex, revision, "project_copy", "创建完整且独立的工程副本", json.dumps(summary, ensure_ascii=False)))
        db.commit()
    manifests = []
    current_root = staged.extraction / "assistant" / "chats"
    for path in current_root.glob("*/manifest.json"):
        current = json.loads(path.read_text(encoding="utf-8"))
        if current.get("project_id") != source_project_id:
            raise ValueError("源当前会话属于另一工程，未复制不一致会话")
        current["project_id"] = project_id
        current["sdk_directory"] = str(destination.extraction / "assistant")
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        manifests.append(path.relative_to(staged.extraction).as_posix())
    return changes, external, manifests


def _cleanup_owned(paths):
    for path in reversed(paths):
        # Every target is an exact, newly claimed output path, never a computed
        # ancestor or a source path. Do not remove pre-existing destination data.
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def duplicate_graph_project(source, destination):
    from .project_catalog import open_graph_project

    source_entry, destination_entry = Path(source).resolve(), Path(destination).resolve()
    original = open_graph_project(source_entry)
    source_layout = layout(source_entry)
    if destination_entry.suffix.lower() != ".nproj":
        raise ValueError("副本入口必须是新的.nproj文件")
    if destination_entry.exists():
        raise ValueError("目标工程入口已存在，不能覆盖")
    target = layout(destination_entry)
    targets = [target.root, target.extraction, target.chats]
    source_roots = list(_roots(source_layout).values())
    if any(_overlap(path, root) for path in [destination_entry, *targets] for root in source_roots) or destination_entry == source_entry:
        raise ValueError("副本路径与源工程数据或附件目录重叠，请选择独立位置")
    if any(path.exists() for path in targets):
        raise ValueError("目标工程附属目录已存在，不能覆盖")
    target.entry.parent.mkdir(parents=True, exist_ok=True)
    stage_path = Path(tempfile.mkdtemp(prefix=".project-copy-", dir=target.entry.parent)).resolve()
    # Mirror final relative names, so the fully written staged descriptor is
    # already correct when atomically linked into the destination directory.
    def stage_location(path):
        return stage_path / path.relative_to(target.entry.parent)
    staged = Layout(stage_location(target.root), stage_location(target.db), stage_location(target.material),
                    stage_location(target.extraction), stage_location(target.chats), stage_location(target.entry))
    owned = []
    report = {"source": str(source_entry), "destination": str(destination_entry), "excluded_temporary_tool_cache": True,
              "copied_components": ["database", "data", "material", "Library", "extraction", "chats", "sdk_context"]}
    try:
        with ExitStack() as stack:
            sdk = source_layout.extraction / "assistant"
            sdk.mkdir(parents=True, exist_ok=True)
            source_layout.material.mkdir(parents=True, exist_ok=True)
            for path in (sdk / "task.lock", sdk / "business-actions.lock", source_layout.material / ".write.lock"):
                try:
                    lease = ProjectLease(path)
                except OSError as exc:
                    raise ValueError("源工程正在执行任务或写入资料，请完成或停止后再创建副本") from exc
                stack.callback(lease.release)
            description_before = source_entry.read_bytes()
            provider = ProviderStore()
            provider_path = provider.path.resolve()
            inventory = _inventory(source_layout, provider_path)
            databases = {source_layout.db} | {path for path in inventory.values() if _sqlite(path)}
            for path in sorted(databases, key=str):
                _lock_database(stack, path)
            for root in _roots(staged).values():
                root.mkdir(parents=True, exist_ok=True)
            _backup_sqlite(source_layout.db, staged.db)
            hashes = {}
            for key, path in inventory.items():
                if path == source_layout.db:
                    continue
                component, relative = key
                output = _roots(staged)[component] / relative
                if path in databases:
                    _backup_sqlite(path, output)
                else:
                    hashes[key] = _copy_file(path, output)
            if source_entry.read_bytes() != description_before or _inventory(source_layout, provider_path) != inventory:
                raise ValueError("复制期间工程文件布局发生变化，未发布副本")
            if any(_hash(inventory[key]) != digest for key, digest in hashes.items()):
                raise ValueError("复制期间源附件发生变化，未发布副本")
            project_id = uuid4().hex
            bases, external, manifests = _update_current_identity(staged, source_layout, target, project_id)
            report.update(project_id=project_id, rebased_writing_basis_ids=bases, current_session_manifests=manifests,
                          external_manifest_references=external,
                          diagnostics=[{"code": "HISTORICAL_PATHS_PRESERVED", "message": "历史任务、原始SDK文件及聊天文本原样复制，其中原始绝对路径仍作为历史证据保留；当前会话入口与当前正文依据已指向副本。旧聊天中的临时工具依据编号须重新读取。"}])
            write_project(staged.entry, title=target.entry.stem, project_id=project_id, locations=staged)
            with provider.copy_scope(original.graph_project_info()["project_id"], project_id) as keys:
                report["copied_provider_keys"] = keys
                # Atomically claim each destination directory before populating
                # it; never merge into a user's existing directory.
                for staged_root, final_root in ((staged.root, target.root), (staged.extraction, target.extraction), (staged.chats, target.chats)):
                    final_root.mkdir(exist_ok=False)
                    owned.append(final_root)
                    for child in staged_root.iterdir():
                        child.rename(final_root / child.name)
                # Same-volume hard-link publication is atomic and never replaces
                # a pre-existing entry. Partial descriptor writes stay in stage.
                os.link(staged.entry, target.entry)
                owned.append(target.entry)
                copied = open_graph_project(target.entry)
            copied._copy_report = report
            return copied
    except BaseException:
        _cleanup_owned(owned)
        raise
    finally:
        shutil.rmtree(stage_path, ignore_errors=True)
