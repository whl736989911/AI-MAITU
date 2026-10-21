from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from octop.infra.agents.memory_context import (
    ManagedMemoryContext,
    MemoryContextConflict,
    read_workspace_document,
    save_workspace_document,
)
from octop.infra.agents.middleware import feature_memory, manual_memory
from octop.infra.agents.middleware.feature_memory import FeatureMemoryMiddleware
from octop.infra.agents.middleware.manual_memory import (
    ExpertManualMemoryGuardMiddleware,
    PrivateManualMemoryMiddleware,
    WorkspaceManualMemoryMiddleware,
)


class Workspace:
    def __init__(self, files=None):
        self.files = dict(files or {})

    def read_text(self, path):
        return self.files.get(path)

    def write_text(self, path, text, *, force=False):
        self.files[path] = text

    def exists(self, path):
        return path in self.files


def test_manual_memory_write_requires_current_snapshot():
    workspace = Workspace()
    original = read_workspace_document(workspace, "MEMORY.md")
    saved = save_workspace_document(
        workspace, "MEMORY.md", "reviewed change\n", expected_snapshot=original.snapshot
    )
    assert saved.text == "reviewed change\n"
    with pytest.raises(MemoryContextConflict):
        save_workspace_document(
            workspace, "MEMORY.md", "stale overwrite\n", expected_snapshot=original.snapshot
        )
    assert read_workspace_document(workspace, "MEMORY.md").text == "reviewed change\n"


def test_clinical_profile_user_document_is_not_manually_editable():
    workspace = Workspace(
        {
            "scripts/clinical_profile.py": "# generated profile owner\n",
            "USER.md": "generated\n",
        }
    )
    document = read_workspace_document(workspace, "USER.md")
    assert not document.editable
    with pytest.raises(ManagedMemoryContext):
        save_workspace_document(
            workspace, "USER.md", "manual change\n", expected_snapshot=document.snapshot
        )
    assert workspace.read_text("USER.md") == "generated\n"


def test_workspace_manual_snapshot_is_first_use_and_seals_empty_state():
    class Request:
        def __init__(self, state, system_message=None):
            self.state = state
            self.system_message = system_message

        def override(self, **values):
            return Request(self.state, values["system_message"])

    workspace = Workspace()
    middleware = WorkspaceManualMemoryMiddleware(agent_id="expert", workspace=workspace)
    first = middleware.before_agent({}, None)
    assert first["octop_workspace_manual_docs"]["__sealed__"] == ""
    workspace.files["MEMORY.md"] = "added later"
    assert middleware.before_agent(first, None) is None
    assert first["octop_workspace_manual_docs"]["MEMORY.md"] == ""

    fresh = WorkspaceManualMemoryMiddleware(agent_id="expert", workspace=workspace)
    current = fresh.before_agent({}, None)
    injected = fresh._modify(Request(current))
    assert "added later" in str(injected.system_message.content)


def test_expert_file_guard_compares_stamped_snapshot_to_current_document():
    workspace = Workspace({"MEMORY.md": "manual"})
    middleware = ExpertManualMemoryGuardMiddleware(agent_id="expert", workspace=workspace)
    snapshot = WorkspaceManualMemoryMiddleware(agent_id="expert", workspace=workspace).before_agent(
        {}, None
    )
    request = type(
        "Request",
        (),
        {
            "tool_call": {"name": "edit_file", "id": "call", "args": {"file_path": "MEMORY.md"}},
            "state": snapshot,
        },
    )()
    assert middleware._refusal(request) is None
    workspace.files["MEMORY.md"] = "dashboard edit"
    refusal = middleware._refusal(request)
    assert refusal is not None and refusal.status == "error"
    assert "changed after this session loaded it" in refusal.content


def test_private_manual_snapshot_isolated_by_verified_namespace(monkeypatch):
    class Memory:
        def __init__(self, text):
            self.text = text

        def get_meta(self, key):
            if key.endswith("USER.md"):
                return '{"schema":1,"revision":1,"text":"' + self.text + '"}'
            return None

    class Runtime:
        def service(self, namespace):
            return type("Service", (), {"memory": Memory(namespace)})()

    namespace = {"value": "agent_feature_user_a"}
    monkeypatch.setattr(
        manual_memory,
        "turn_feature_memory_context",
        lambda: type("Context", (), {"private_namespace": namespace["value"]})(),
    )
    middleware = PrivateManualMemoryMiddleware(agent_id="feature", runtime=Runtime())
    first = middleware.before_agent({}, None)
    assert first["octop_private_manual_docs"]["USER.md"] == namespace["value"]

    class Request:
        def __init__(self, state, system_message=None):
            self.state = state
            self.system_message = system_message

        def override(self, **values):
            return Request(self.state, values["system_message"])

    injected = middleware._modify(Request(first))
    assert namespace["value"] in str(injected.system_message.content)

    namespace["value"] = "agent_feature_user_b"
    second = middleware.before_agent(first, None)
    assert second["octop_private_manual_docs"]["USER.md"] == namespace["value"]
    monkeypatch.setattr(manual_memory, "turn_feature_memory_context", lambda: None)
    sealed = middleware.before_agent(second, None)
    assert sealed["octop_private_manual_docs"] == {"__sealed__": ""}


def test_native_memory_sources_keep_nonmanual_files_and_bootstrap_suppression():
    assert manual_memory.filter_native_memory_sources(("AGENTS.md", "USER.md", "custom.md")) == (
        "AGENTS.md",
        "custom.md",
    )
    assert manual_memory.filter_native_memory_sources(()) == ()
    assert manual_memory.filter_native_memory_sources(None) == ("AGENTS.md", "SOUL.md")


def test_feature_recall_does_not_repeat_manual_document_text(monkeypatch):
    class Service:
        def recall(self, *args, **kwargs):
            return type("Recall", (), {"rendered": "Recall: manual note; useful separate fact"})()

    class Runtime:
        def service(self, namespace):
            return Service()

    monkeypatch.setattr(feature_memory, "_get_configurable", lambda runtime: {})
    middleware = FeatureMemoryMiddleware(agent_id="feature", runtime=Runtime())
    rendered = middleware._recall_rendered(
        ("shared",),
        HumanMessage(content="question"),
        None,
        manual_documents=["manual note"],
    )
    assert rendered == "Recall: ; useful separate fact"
