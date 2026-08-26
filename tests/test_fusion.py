from layouttrace.retrieval.fusion import reciprocal_rank_fusion


def test_rrf_rewards_agreement():
    dense = ["a", "b", "c"]
    lexical = ["b", "a", "d"]
    fused = dict(reciprocal_rank_fusion([dense, lexical], k=60))
    # "a" and "b" appear high in both -> above "c"/"d" which appear in only one
    assert fused["a"] > fused["c"]
    assert fused["b"] > fused["d"]


def test_rrf_order_and_weights():
    r1 = ["x", "y"]
    r2 = ["y", "x"]
    ranked = reciprocal_rank_fusion([r1, r2], k=1, weights=[1.0, 3.0])
    # r2 weighted 3x, and it ranks "y" first -> "y" wins
    assert ranked[0][0] == "y"


def test_rrf_empty():
    assert reciprocal_rank_fusion([[], []]) == []
