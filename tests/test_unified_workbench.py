"""Removed workbenches have no residual public workflow; unified facts remain."""
import json
from pathlib import Path

from narrative import tool_api
from narrative.native_bridge import NativeBridge
from narrative.operations import Operations


def test_removed_workbenches_and_business_entrypoints_are_absent(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'removed.nproj')
    qml = Path(__file__).parents[1] / 'src/narrative/qml'
    for name in ('ExtractPage.qml', 'PropertyPage.qml', 'ReviewPage.qml'):
        assert not (qml / name).exists()
    for name in ('extract_slice', 'load_slice_extraction', 'commit_slice_extraction'):
        assert not hasattr(ops, name)
    for name in ('startSliceExtraction', 'reviewSliceExtraction', 'commitSliceExtraction'):
        assert not hasattr(NativeBridge, name)
    assert {name for name in tool_api.TOOLS if 'extract' in name} == {'preview_extraction', 'submit_extraction'}
    main = (qml / 'Main.qml').read_text(encoding='utf-8')
    assert 'ExtractPage' not in main and 'PropertyPage' not in main and 'ReviewPage' not in main
    assert all(name in tool_api.TOOLS for name in ('read_source', 'node_properties', 'submit_extraction'))

    schema = ops.project.schema()
    serialized = json.dumps(schema)
    assert 'two_stage' not in serialized and 'human-only' not in serialized
    assert 'submit_extraction' in schema['extraction_submission']['submit']
