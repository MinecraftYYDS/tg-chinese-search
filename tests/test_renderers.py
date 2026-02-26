from app.interaction.renderers import render_private_result
from app.storage.repository import SearchRow


def _row() -> SearchRow:
    return SearchRow(
        id=1,
        chat_id=-1001234567890,
        message_id=42,
        channel_username="demo_channel",
        source_link=None,
        text="这是一个测试文本，用于关键词命中。",
        timestamp=1700000000,
    )


def test_render_private_result_without_ids() -> None:
    text = render_private_result(_row(), ["测试"], include_message_ids=False)
    assert "🆔" not in text
    assert "-1001234567890:42" not in text


def test_render_private_result_with_ids() -> None:
    text = render_private_result(_row(), ["测试"], include_message_ids=True)
    assert "🆔" in text
    assert "-1001234567890:42" in text
