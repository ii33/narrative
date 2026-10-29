"""Provider settings round-trip: per-project storage, provider switch, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from narrative.operations import Operations


def test_provider_settings_round_trip_and_provider_switch(tmp_path: Path) -> None:
    ops = Operations.create_graph_project(tmp_path / "settings.nproj")

    saved = ops.save_provider_profile({
        "provider": "ark",
        "model": "ark-code-latest",
        "context_window": 900_000,
        "output_reserve": 0,
        "compaction_reserve": 50_000,
        "compaction_max_tokens": 90_000,
        "max_tokens": 200_000,
        "timeout_seconds": 600,
    })
    assert saved["context_window"] == 900_000

    # The active profile keeps the saved numbers.
    active = ops.harness_settings()
    assert active["provider"] == "ark"
    assert active["context_window"] == 900_000
    assert active["output_reserve"] == 0
    assert active["compaction_max_tokens"] == 90_000
    assert active["max_tokens"] == 200_000

    # Switching to another provider reads that provider's own record and does
    # not change which profile is active.
    other = ops.harness_settings("qwen")
    assert other["provider"] == "qwen"
    assert ops.harness_settings()["provider"] == "ark"

    # Coming back restores the ark numbers.
    again = ops.harness_settings("ark")
    assert again["context_window"] == 900_000
    assert again["compaction_max_tokens"] == 90_000


def test_provider_settings_reject_reserves_above_the_window(tmp_path: Path) -> None:
    ops = Operations.create_graph_project(tmp_path / "invalid.nproj")

    with pytest.raises(ValueError, match="有效输出预留"):
        ops.save_provider_profile({
            "provider": "ark",
            "context_window": 200_000,
            "output_reserve": 150_000,
            "compaction_reserve": 60_000,
        })


def test_provider_settings_require_exact_integers(tmp_path: Path) -> None:
    ops = Operations.create_graph_project(tmp_path / "floats.nproj")

    with pytest.raises(ValueError):
        ops.save_provider_profile({"provider": "ark", "context_window": 1.5})
