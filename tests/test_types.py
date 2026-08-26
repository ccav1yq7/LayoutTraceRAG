from layouttrace.types import Citation, EvidenceNode, format_timecode


def test_format_timecode():
    assert format_timecode(150) == "00:02:30"
    assert format_timecode(2530, 2568) == "00:42:10-00:42:48"


def test_node_timecode_and_citation():
    n = EvidenceNode(id="v:transcript:1", video_id="v", modality="transcript",
                     text="  混合检索是这样实现的 …  ", start_s=2530, end_s=2568)
    assert n.timecode == "00:42:10-00:42:48"
    c = Citation.from_node(n)
    assert c.timecode == n.timecode
    assert c.node_id == n.id
    assert "\n" not in c.snippet
