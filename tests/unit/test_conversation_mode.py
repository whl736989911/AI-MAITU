from octop.infra.agents.conversation_mode import (
    default_conversation_mode_from_config,
    resolve_conversation_mode,
)


def test_mode_precedence_is_explicit_then_sticky_then_agent_default() -> None:
    assert (
        resolve_conversation_mode(explicit="ask", thread_mode="plan", agent_default="craft")
        == "ask"
    )
    assert resolve_conversation_mode(thread_mode="plan", agent_default="ask") == "plan"
    assert resolve_conversation_mode(agent_default="ask") == "ask"
    assert resolve_conversation_mode() == "craft"


def test_invalid_explicit_mode_retains_craft_fallback() -> None:
    assert resolve_conversation_mode(explicit="invalid", thread_mode="plan") == "craft"
    assert default_conversation_mode_from_config({"conversation_mode": "invalid"}) == "craft"
    assert default_conversation_mode_from_config({"conversation_mode": "plan"}) == "plan"
