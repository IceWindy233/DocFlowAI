from docflow.services.evaluation import character_error_rate, classification_metrics


def test_character_error_rate() -> None:
    assert character_error_rate("横琴公文", "横琴公文") == 0
    assert character_error_rate("横琴公文", "横琴文件") == 0.5


def test_classification_metrics() -> None:
    result = classification_metrics(["函", "函", "请示"], ["函", "请示", "请示"])
    assert result.accuracy == 2 / 3
    assert round(result.f1, 4) == 0.6667
