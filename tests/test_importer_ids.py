from app.importer.telegram_json import _to_bot_api_chat_id


def test_export_chat_id_convert_to_bot_api_format() -> None:
    assert _to_bot_api_chat_id(3116930533) == -1003116930533
    assert _to_bot_api_chat_id(-1003116930533) == -1003116930533

