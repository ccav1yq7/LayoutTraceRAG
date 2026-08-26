"""Step 5 — cross-modal visual retrieval: text query → frames by visual content."""
from layouttrace.config import Config
from layouttrace.index import (
    HashEmbedder,
    HashVisionEmbedder,
    InMemoryStore,
    VisualSearcher,
    build_retriever,
)
from layouttrace.types import EvidenceNode


def test_visual_searcher_cross_modal():
    vs = VisualSearcher(HashVisionEmbedder())
    vs.add_image("f:whiteboard", "person writing equations on a whiteboard")
    vs.add_image("f:cooking", "a chef frying vegetables in a kitchen")
    assert vs.search("equations whiteboard lecture", 1) == ["f:whiteboard"]
    assert vs.search("frying vegetables kitchen", 1) == ["f:cooking"]


def test_visual_ranking_joins_fusion():
    """A frame whose OCR text lacks the query words is pulled into top-k visually."""
    query = "hybrid retrieval diagram"
    nodes = [  # distractors that DO match the query lexically → fill the text ranking
        EvidenceNode(id=f"v:t:{i}", video_id="v", modality="transcript",
                     text="hybrid retrieval diagram overview part " + str(i), start_s=i, end_s=i + 5)
        for i in range(6)
    ]
    nodes.append(EvidenceNode(id="v:f:1", video_id="v", modality="frame",
                              text="[frame]", start_s=900, end_s=900))  # no query words
    store = InMemoryStore(HashEmbedder())
    store.add(nodes)

    text_only = build_retriever(store, Config())
    ids_text = [n.id for n in text_only.retrieve(query, 10)]
    assert "v:f:1" not in ids_text[:3]           # weak on text alone

    visual = VisualSearcher(HashVisionEmbedder())
    visual.add_image("v:f:1", "hybrid retrieval architecture diagram dense bm25")
    multimodal = build_retriever(store, Config(), visual=visual)
    ids_mm = [n.id for n in multimodal.retrieve(query, 10)]
    # the cross-modal signal lifts the frame's rank
    assert ids_mm.index("v:f:1") < ids_text.index("v:f:1")
