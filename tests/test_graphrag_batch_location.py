"""GraphRAG preview keeps its read-only role with the batched location contract."""
import json

from narrative.graph_store import GraphStore
from narrative.ms_graphrag_adapter import EXPORT_SCHEMA
from narrative.operations import Operations


def test_sidecar_quotes_are_located_in_one_source_read_without_evidence_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRATIVE_PROVIDER_DB", str(tmp_path/"providers.sqlite3"))
    ops=Operations.create_graph_project(tmp_path/"graph.nproj")
    sid=ops.import_source("原文", "😀甲出门。乙停下。")
    before=ops.graph_snapshot()
    export=tmp_path/"export.json"
    export.write_text(json.dumps({"schema":EXPORT_SCHEMA,"method":"standard","tables":{
        "documents":[{"id":"doc"}],"text_units":[{"id":"unit","document_id":"doc"}],
        "covariates":[{"id":str(i),"text_unit_id":"unit","source_text":quote}
                      for i,quote in enumerate(["甲出门。","乙停下。","原文不存在"])]}},ensure_ascii=False),encoding="utf-8")
    original=GraphStore.source
    reads=[]
    def counted(self, source_id):
        reads.append(source_id)
        return original(self,source_id)
    monkeypatch.setattr(GraphStore,"source",counted)
    result=ops.graphrag_preview(export,{"doc":sid})
    assert reads == [sid]
    assert [row["exact_quote_eligible"] for row in result["claims"]] == [True,True,False]
    assert result["claims"][0]["locate_matches"] == [{"start":1,"end":5}]
    assert all("id" not in match for row in result["claims"] for match in row["locate_matches"])
    assert result["formal_records_created"] == 0
    assert ops.graph_snapshot() == before
