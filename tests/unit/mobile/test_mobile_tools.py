"""Unit tests for mobile tool deployment lockdown."""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from langgraph.config import var_child_runnable_config

from octop.config import CapabilitiesConfig, MobileCapabilities, OctopConfig
from octop.infra.errors import ErrorCode
from octop.infra.mobile.tools import build_mobile_tools


@contextmanager
def _configurable(**kwargs: object):
    token = var_child_runnable_config.set({"configurable": kwargs})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


def _enabled_config() -> OctopConfig:
    return OctopConfig(
        capabilities=CapabilitiesConfig(
            mobile=MobileCapabilities(
                enabled=True,
                backend="physical",
                probed_at="2026-01-01T00:00:00Z",
            )
        )
    )


def test_build_mobile_tools_empty_when_disabled() -> None:
    assert build_mobile_tools(OctopConfig(), user_repo=MagicMock(), org_unit_repo=MagicMock()) == []


@pytest.mark.parametrize("is_admin", [False, True], ids=["ordinary-user", "system-admin"])
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("mobile_screenshot", {}),
        ("mobile_tap", {"x": 10, "y": 20}),
        ("mobile_swipe", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}),
        ("mobile_launch_app", {"package": "com.example.app"}),
        ("mobile_ui_dump", {}),
        ("mobile_handoff_to_user", {"reason": "login"}),
    ],
)
@pytest.mark.asyncio
async def test_mobile_tools_are_deployment_restricted_before_device_access(
    is_admin: bool, tool_name: str, arguments: dict[str, object]
) -> None:
    with patch("octop.infra.mobile.tools.find_adb", return_value="/adb"):
        tools = build_mobile_tools(
            _enabled_config(), user_repo=MagicMock(), org_unit_repo=MagicMock()
        )
    tool = next(item for item in tools if item.name == tool_name)

    with (
        _configurable(user="1", user_is_admin=is_admin, locale="en", agent_id="agent1"),
        patch("octop.infra.mobile.tools.list_devices") as list_devices,
        patch("octop.infra.mobile.tools.screencap_png") as screencap,
        patch("octop.infra.mobile.tools.tap") as tap,
        patch("octop.infra.mobile.tools.swipe") as swipe,
        patch("octop.infra.mobile.tools.shell") as shell,
    ):
        result = json.loads(await tool.ainvoke(arguments))

    assert result["error"]["code"] == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED.value
    list_devices.assert_not_called()
    screencap.assert_not_called()
    tap.assert_not_called()
    swipe.assert_not_called()
    shell.assert_not_called()
