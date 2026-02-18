from app.normalize.channel_message import extract_text_field


def test_extract_text_field_string() -> None:
    assert extract_text_field("你好") == "你好"


def test_extract_text_field_array_mixed() -> None:
    value = ["你", {"type": "bold", "text": "好"}, "世界"]
    assert extract_text_field(value) == "你好世界"

