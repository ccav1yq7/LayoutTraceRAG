"""Real embedded LanceDB integration, without model downloads."""
import pytest
pytest.importorskip("lancedb")

from layouttrace.config import Config
from layouttrace.index import HashEmbedder, LanceStore
from layouttrace.types import EvidenceNode


def test_index_reopens_upserts_and_rejects_changed_embedder(tmp_path):
    cfg = Config(db_path=str(tmp_path / "index"), embed_model="hash-demo")
    store = LanceStore(cfg, HashEmbedder())
    assert store.dense.search("topic", 8) == []
    node = EvidenceNode(id="one", video_id="v", modality="transcript", text="topic", start_s=0, end_s=2)
    store.add([node])
    store.add([node.model_copy(update={"text": "updated topic"})])
    again = LanceStore(cfg, HashEmbedder())
    assert again.lookup("one").text == "updated topic"
    assert again._table.count_rows() == 1
    assert again.lexical.search("topic", 8) == ["one"]
    assert again.dense.search("topic", 8) == ["one"]
    with pytest.raises(ValueError, match="identity"):
        LanceStore(cfg, HashEmbedder(dim=32))
    assert again._table.count_rows() == 1


def test_legacy_table_is_preserved_and_requires_new_table(tmp_path):
    import lancedb
    cfg = Config(db_path=str(tmp_path / "index"), embed_model="hash-demo")
    table = lancedb.connect(cfg.db_path).create_table(cfg.table, data=[{"id": "legacy", "vector": [0.0, 1.0]}])
    with pytest.raises(ValueError, match="Legacy index"):
        LanceStore(cfg, HashEmbedder())
    assert table.count_rows() == 1
