"""Remote browser keyboard protocol dispatches text and native editing keys."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from octop.api.routers.browser.stream import _handle_client_event


def _session() -> SimpleNamespace:
    return SimpleNamespace(_internal=SimpleNamespace(client=SimpleNamespace(send=AsyncMock())))


@pytest.mark.asyncio
async def test_enter_is_a_real_keypress_with_cr_text() -> None:
    sess = _session()
    await _handle_client_event(
        sess, {"type": "keydown", "key": "Enter", "code": "Enter", "keyCode": 13}
    )
    down, up = sess._internal.client.send.await_args_list
    assert down.args[1]["type"] == "keyDown"
    assert down.args[1]["text"] == "\r"
    assert down.args[1]["unmodifiedText"] == "\r"
    assert up.args[1]["type"] == "keyUp"


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "mask"), [("metaKey", 4), ("ctrlKey", 2)])
async def test_select_all_is_platform_independent(field: str, mask: int) -> None:
    sess = _session()
    await _handle_client_event(
        sess,
        {
            "type": "keydown",
            "key": "a",
            "code": "KeyA",
            "keyCode": 65,
            field: True,
        },
    )
    down, up = sess._internal.client.send.await_args_list
    assert down.args[1]["commands"] == ["selectAll"]
    assert down.args[1]["modifiers"] == mask
    assert up.args[1]["windowsVirtualKeyCode"] == 65


@pytest.mark.asyncio
async def test_uncommitted_ime_keys_are_ignored() -> None:
    sess = _session()
    for key in ("Process", "Dead", "Unidentified"):
        await _handle_client_event(sess, {"type": "keydown", "key": key})
    sess._internal.client.send.assert_not_awaited()
