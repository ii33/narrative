"""仓库里不许出现任何一个具体工程的内容。

发布的是工具，不是某本书的工作现场。这条规则以前只写在 `docs/PUBLIC_REPOSITORY.md`
里，靠人记得；现在由这条用例盯着：只要有人把工程库、提取缓存、会话、截图或原文人名
带进仓库，它就失败。

判据是「具体工程的名字」，不是「所有中文」——示例文本（`src/narrative/sample.txt`）
是合成人物，应该留下。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 拆开写：这条用例本身不该命中自己。
MARKERS = [
    "飘" + "渺",
    "李" + "强",
    "小" + "玉",
    "曾" + "伟光",
    "傅" + "山",
    "花" + "媚娘",
    "彭" + "子东",
    "侯" + "霹净",
    "潭" + "博手镯",
    "紫" + "炎心",
    "天" + "庭星",
    "封" + "缘星",
    "piao" + "miao",
]

# 工程本体：库、提取缓存、会话、资料。这些永远不该被跟踪。
PROJECT_PATHS = ("data/", "_extraction/", "_chats/", "_material/", "_projects/")

TEXT_SUFFIXES = {".py", ".md", ".json", ".qml", ".toml", ".txt", ".ps1", ".cmd", ".yml"}


def _tracked() -> list[str]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):  # 不是 git 检出（比如 sdist）
        pytest.skip("not a git checkout")
    return [name for name in listed.decode("utf-8").split("\0") if name]


def test_no_project_database_or_working_directory_is_tracked() -> None:
    offenders = [name for name in _tracked() if name.startswith(PROJECT_PATHS)]
    assert offenders == []


def test_no_tracked_file_mentions_a_specific_project() -> None:
    hits: list[str] = []
    for name in _tracked():
        path = ROOT / name
        if path.suffix not in TEXT_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found = [marker for marker in MARKERS if marker in text]
        if found:
            hits.append(f"{name}: {', '.join(found)}")
    assert hits == [], "这些文件里还带着具体工程的内容：\n" + "\n".join(hits)


def test_the_wheel_carries_code_only() -> None:
    """发布包里只有代码：原文、库、缓存都不能被打进 wheel。"""
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]
    excludes = " ".join(wheel.get("exclude", []))
    assert "texts" in excludes
    assert "*.sqlite*" in excludes or "sqlite" in excludes
