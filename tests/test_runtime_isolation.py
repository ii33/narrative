"""Extraction must launch its own binary even in a polluted shell environment."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from narrative.harness import HarnessRunner, HarnessSettings, project_runtime


def test_project_runtime_ignores_system_node_and_cache_switches(tmp_path, monkeypatch):
    pollution = {
        "PATH": "external-node-and-dsh",
        "DSH_RUNTIME_MODE": "node",
        "DSH_HOME": "external-home",
        "NODE_PATH": "external-modules",
        "NODE_OPTIONS": "--require=external-hook",
        "NARB_DISABLE_NATIVE_CACHE": "1",
        "NARB_NATIVE_CACHE_DIR": "external-cache",
        "NARB_BACKEND": "invalid",
    }
    for key, value in pollution.items():
        monkeypatch.setenv(key, value)
    binary, env = project_runtime(tmp_path)
    assert binary.is_file()
    assert env["DSH_RUNTIME_MODE"] == "exe"
    assert env["NODE_PATH"] == env["NODE_OPTIONS"] == ""
    assert env["DSH_HOME"] == ""
    assert env["NARB_DISABLE_NATIVE_CACHE"] == "0"
    assert env["NARB_BACKEND"] == "auto"
    assert Path(env["NARB_NATIVE_CACHE_DIR"]).is_relative_to(Path(sys.prefix))
    assert Path(env["NARB_NATIVE_CACHE_DIR"]).is_dir()
    assert env["PATH"].split(os.pathsep)[0] == str(binary.parent)
    assert "external" not in env["PATH"]
    assert all(os.environ[key] == value for key, value in pollution.items())


def test_external_binary_is_rejected_without_a_system_fallback(tmp_path, monkeypatch):
    import deepseek_harness_runtime

    monkeypatch.setattr(deepseek_harness_runtime, "bundled_runtime_path", lambda: tmp_path / "dsh.exe")
    with pytest.raises(ValueError, match="不能使用外部程序"):
        project_runtime(tmp_path)


def test_native_cache_does_not_grow_with_slice_round_paths(tmp_path):
    deep = tmp_path / "project_extraction" / "slices" / ("a" * 64) / "rounds" / "20261101T120000000000Z-12345678" / "read"
    _, shallow_env = project_runtime(tmp_path)
    _, deep_env = project_runtime(deep)
    assert deep_env["NARB_NATIVE_CACHE_DIR"] == shallow_env["NARB_NATIVE_CACHE_DIR"]
    assert deep_env["DSH_RESOURCE_CACHE"] == shallow_env["DSH_RESOURCE_CACHE"]
    assert deep_env["TMP"] != shallow_env["TMP"]


def test_harness_receives_explicit_project_binary_and_local_cache(tmp_path, monkeypatch):
    import deepseek_harness_runtime

    monkeypatch.setenv("DSH_RUNTIME_MODE", "node")
    captured = {}
    runner = HarnessRunner.__new__(HarnessRunner)
    runner.settings = HarnessSettings()
    runner._gateway = SimpleNamespace(base_url="http://127.0.0.1", token="local-test-token")

    def harness_type(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    runner._initialize_harness(harness_type, tmp_path)
    assert captured["dsh_bin"] == str(deepseek_harness_runtime.bundled_runtime_path().resolve())
    assert captured["dsh_home"] == str(tmp_path / "home")
    assert Path(captured["env"]["NARB_NATIVE_CACHE_DIR"]).is_relative_to(Path(sys.prefix))
    assert captured["env"]["DSH_RUNTIME_MODE"] == "exe"
