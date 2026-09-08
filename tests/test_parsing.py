import pytest

from line_assistant.core.errors import DomainError
from line_assistant.ledger.parsing import parse_batch_names, parse_twd_amount


def test_parse_batch_names_accepts_chinese_separators_and_deduplicates() -> None:
    assert parse_batch_names("早餐、午餐，晚餐\n早餐") == ["早餐", "午餐", "晚餐"]


@pytest.mark.parametrize("text", ["跳過", "略過", "  "])
def test_parse_batch_names_can_skip(text: str) -> None:
    assert parse_batch_names(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [("１２０", 120), ("NT$ 1,200", 1200), ("350元", 350), ("$99", 99)],
)
def test_parse_twd_amount_normalizes_common_inputs(text: str, expected: int) -> None:
    assert parse_twd_amount(text) == expected


@pytest.mark.parametrize("text", ["0", "-1", "10.5", "一百", ""])
def test_parse_twd_amount_rejects_non_positive_integer(text: str) -> None:
    with pytest.raises(DomainError):
        parse_twd_amount(text)
