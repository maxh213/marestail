from marestail import pipeline


def test_pipeline_ends_at_qa() -> None:
    assert pipeline.names()[-1] == "qa"
