from __future__ import annotations

import json

from octop.infra.connectors.gateway.adapters.qq_mail import _decode_payload, _parse_message


def test_raw_non_ascii_headers_are_json_serializable() -> None:
    message = _parse_message(
        "From: 张三 <zhangsan@example.com>\nSubject: 会议纪要\nDate: Thu, 8 Oct 2026 10:00:00 +0800\n\n正文".encode()
    )

    serialized = json.dumps(
        {key: str(message.get(key, "") or "") for key in ("From", "Subject", "Date")},
        ensure_ascii=False,
    )

    assert json.loads(serialized)["Subject"] == "会议纪要"
    assert json.loads(serialized)["From"] == "张三 <zhangsan@example.com>"


def test_invalid_declared_body_charset_falls_back_to_utf8() -> None:
    class Part:
        def get_content_charset(self) -> str:
            return "unknown-8bit"

    assert _decode_payload(Part(), "正文".encode()) == "正文"
