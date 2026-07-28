from studio.workflows.first_cut import _tone_allows_menacing_expression


def test_menacing_tones_preserve_villain_performance_signals():
    assert _tone_allows_menacing_expression(["front_facing", "menacing"])
    assert _tone_allows_menacing_expression(["dominant"])


def test_neutral_tones_keep_default_expression_filter():
    assert not _tone_allows_menacing_expression(["clean", "heroic"])
    assert not _tone_allows_menacing_expression(None)
