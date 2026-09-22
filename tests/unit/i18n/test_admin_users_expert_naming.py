"""Admin Users UI must call agents 「专家」 / Experts (issue #155).

The #155 decision still holds, and this file still pins it — where the name
answers *what kind of agent*. The experts column counts the agents a person
made for themselves, so it is 「专家」 / Experts again.

What #155 did **not** decide is what to call the agents behind features, and
they are why the pin below is narrower than it once was:

* The features column is 「功能」 / Features — the row's ``kind`` says so
  (``src/octop/infra/agents/kinds.py``, mirrored by
  ``dashboard/src/utils/agentKind.ts``), so the two kinds are told apart by
  name instead of sharing one 「智能体」 header that said neither.
* The drawer and its empty state are **not** pinned to 「专家」 any more. It
  lists every agent the user holds — experts *and* the agents their features
  run on — so it says 「智能体」 / agents. Pinning 「专家」 there would call a
  feature's agent an expert, the exact mix-up the ``kind`` column exists to
  prevent. The drawer's copy is a decision about the set it holds, and the set
  changed; the column's copy is a decision about that column's own set, and
  that set is what #155 named.
"""

from __future__ import annotations

import json
from pathlib import Path


def test_admin_users_agent_column_uses_expert_terminology() -> None:
    repo = Path(__file__).resolve().parents[3]
    zh = json.loads((repo / "dashboard/src/locales/zh.json").read_text(encoding="utf-8"))
    en = json.loads((repo / "dashboard/src/locales/en.json").read_text(encoding="utf-8"))
    # One column per kind: the experts' own column keeps #155's name, and the
    # features' agent is named as what it is rather than folded into it.
    assert zh["adminUsers"]["colAgents"] == "专家"
    assert zh["adminUsers"]["colFeatures"] == "功能"
    assert en["adminUsers"]["colAgents"] == "Experts"
    assert en["adminUsers"]["colFeatures"] == "Features"
    # The drawer holds both kinds (it is opened from either column), so its
    # title and empty state may only claim what both of them are.
    assert zh["adminUsers"]["agentsDrawerTitle"] == "{{username}} 的智能体"
    assert zh["adminUsers"]["noAgents"] == "该用户暂无智能体"
    assert en["adminUsers"]["agentsDrawerTitle"] == "{{username}}'s agents"
    assert en["adminUsers"]["noAgents"] == "This user has no agents yet"
