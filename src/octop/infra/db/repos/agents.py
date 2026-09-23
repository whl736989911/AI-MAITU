"""Agent table access."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from octop.infra.agents.kinds import (
    KIND_AGENT,
    feature_agent_id_for,
    feature_id_of_agent,
    is_feature_agent,
)
from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import (
    UNSET,
    DbRow,
    bool_int,
    map_rows,
    now_ts,
    optional_updates,
    sql_in_placeholders,
)
from octop.infra.db.repos.resource_acl import ResourceAclRepo, owner_unit_key
from octop.infra.sharing import VISIBILITY_PRIVATE, VISIBILITY_PUBLIC, allowed_resource_ids


def _opt_str(r: DbRow, key: str) -> str | None:
    try:
        value = r[key]
    except (KeyError, IndexError):
        return None
    if value is None:
        return None
    text = str(value)
    return text if text else None


@dataclass(frozen=True)
class AgentRow:
    id: int
    agent_id: str
    user_id: int | None
    name: str
    description: str | None
    persona_mbti: str | None
    default_model: str | None
    system_prompt: str | None
    enabled: int
    config_json: str | None
    last_state: str | None
    last_error: str | None
    created_at: int
    updated_at: int
    icon: str | None = None
    template_name: str | None = None
    color: str | None = None
    icon_name: str | None = None
    icon_url: str | None = None
    skill_package_ids: str | None = None
    published_expert_id: str | None = None
    welcome_message: str | None = None
    knowledge_base_ids: str | None = None
    mcp_servers: str | None = None
    kind: str = KIND_AGENT
    """What this row is — see :mod:`octop.infra.agents.kinds`. Never empty."""

    @classmethod
    def from_row(cls, r: DbRow) -> AgentRow:
        return cls(
            id=r["id"],
            agent_id=r["agent_id"],
            user_id=r["user_id"],
            name=r["name"],
            description=r["description"],
            persona_mbti=r["persona_mbti"],
            default_model=r["default_model"],
            system_prompt=r["system_prompt"],
            enabled=r["enabled"],
            config_json=r["config_json"],
            last_state=r["last_state"],
            last_error=r["last_error"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            icon=r["icon"],
            template_name=r["template_name"],
            color=_opt_str(r, "color"),
            icon_name=_opt_str(r, "icon_name"),
            icon_url=_opt_str(r, "icon_url"),
            skill_package_ids=_opt_str(r, "skill_package_ids"),
            published_expert_id=_opt_str(r, "published_expert_id"),
            welcome_message=_opt_str(r, "welcome_message"),
            knowledge_base_ids=_opt_str(r, "knowledge_base_ids"),
            mcp_servers=_opt_str(r, "mcp_servers"),
            kind=str(r["kind"]),
        )


class AgentRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db
        self._acl = ResourceAclRepo(db)

    def create(
        self,
        *,
        agent_id: str,
        user_id: int | None,
        name: str,
        description: str | None = None,
        persona_mbti: str | None = None,
        default_model: str | None = None,
        system_prompt: str | None = None,
        config_json: str | None = None,
        icon: str | None = None,
        template_name: str | None = None,
        color: str | None = None,
        icon_name: str | None = None,
        icon_url: str | None = None,
        skill_package_ids: str | None = None,
        published_expert_id: str | None = None,
        welcome_message: str | None = None,
        knowledge_base_ids: str | None = None,
        mcp_servers: str | None = None,
        kind: str = KIND_AGENT,
    ) -> str:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO agents(agent_id, user_id, name, description, "
                "persona_mbti, default_model, system_prompt, enabled, config_json, icon, "
                "template_name, color, icon_name, icon_url, skill_package_ids, "
                "published_expert_id, welcome_message, knowledge_base_ids, mcp_servers, "
                "created_at, updated_at, kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    user_id,
                    name,
                    description,
                    persona_mbti,
                    default_model,
                    system_prompt,
                    config_json,
                    icon,
                    template_name,
                    color,
                    icon_name,
                    icon_url,
                    skill_package_ids,
                    published_expert_id,
                    welcome_message,
                    knowledge_base_ids,
                    mcp_servers,
                    ts,
                    ts,
                    kind,
                ),
            )
            self._acl.set_visibility(
                "agent",
                agent_id,
                VISIBILITY_PRIVATE,
                owner_user_id=user_id,
                unit_key=None if user_id is None else owner_unit_key(conn, user_id),
                conn=conn,
            )
        return agent_id

    def get(self, agent_id: str) -> AgentRow | None:
        with self._db.connect() as conn:
            r = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        return AgentRow.from_row(r) if r else None

    def list_by_user(self, user_id: int, *, include_disabled: bool = True) -> list[AgentRow]:
        sql = "SELECT * FROM agents WHERE user_id = ?"
        if not include_disabled:
            sql += " AND enabled = 1"
        sql += " ORDER BY created_at ASC, id ASC"
        with self._db.connect() as conn:
            rows = conn.execute(sql, (user_id,)).fetchall()
        return map_rows(rows, AgentRow)

    def list_all(self, *, include_disabled: bool = True) -> list[AgentRow]:
        sql = "SELECT * FROM agents"
        if not include_disabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at ASC, id ASC"
        with self._db.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return map_rows(rows, AgentRow)

    def present_kinds(self) -> list[str]:
        """The ``kind``s this table holds, each once — which branches there are.

        The one question a surface that draws one branch per kind asks, and it is
        asked of the whole table: a kind that no row carries has nothing to show,
        while a per-kind question ("is there a row of kind X") would have to be
        asked once per kind and answered from whatever the caller can see. Rows
        that are disabled do not count — the deployment is not running them, and
        the enabled rows are the same population ``AgentManager.list_rows`` hands
        Admin → Users, so the two agree on what this deployment holds.

        Answers in the kinds' own vocabulary (:mod:`octop.infra.agents.kinds`),
        never in a surface's words for them: which kinds are worth a branch is
        this method's answer, what to call the branch is the caller's.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT kind FROM agents WHERE enabled = 1 ORDER BY kind"
            ).fetchall()
        return [str(r["kind"]) for r in rows]

    def set_enabled(self, agent_id: str, enabled: bool) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET enabled = ?, updated_at = ? WHERE agent_id = ?",
                (bool_int(enabled), now_ts(), agent_id),
            )

    def set_shared(self, agent_id: str, shared: bool) -> None:
        """Publish or unpublish an agent to every logged-in user.

        Visibility lives in ``resource_acl`` alone: schema v21 dropped the
        legacy ``is_shared`` column, so there is nothing left to mirror.
        """
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT user_id FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if row is None:
                return
            self._acl.set_visibility(
                "agent",
                agent_id,
                VISIBILITY_PUBLIC if shared else VISIBILITY_PRIVATE,
                owner_user_id=row["user_id"],
                unit_key=None,
                conn=conn,
            )

    def public_agent_ids(self, agent_ids: Collection[str] | None = None) -> set[str]:
        """Ids of agents published to everyone, per ``resource_acl``.

        The display counterpart of ``list_visible``: rows are not needed to say
        whether an agent is shared. A feature published through its *own* entry
        publishes the agent that carries it — the two names of one resource — so
        both are read. When the caller narrows to some rows, only those rows'
        features are asked, and a plain agent asks for nothing extra.
        """
        public = self._acl.public_resource_ids("agent", resource_ids=agent_ids)
        feature_ids = (
            None
            if agent_ids is None
            else [
                feature_id
                for feature_id in map(feature_id_of_agent, agent_ids)
                if feature_id is not None
            ]
        )
        for feature_id in self._acl.public_resource_ids("feature", resource_ids=feature_ids):
            public.add(feature_agent_id_for(feature_id))
        return public

    def list_visible(self, user_id: int, *, exclude_user_id: int | None = None) -> list[AgentRow]:
        """Enabled agents this account may use, per the one access rule.

        The verdict comes from ``sharing.allowed_resource_ids`` over the ACL
        entries — the same rule the single-row check resolves through
        (:func:`octop.api.common.agent.agent_access_entries`), so a list and a
        direct open cannot disagree about an agent. Two names can name one agent
        here: a feature's own entry (``resource_type='feature'``, keyed by the
        feature id) decides the agent that carries it, ``feat-<feature_id>``, so
        those entries are resolved to their agent id before the rows are read.

        Deliberately not "the published set": a directed grant, a unit share and
        a role grant all put an agent in this list, which is exactly what the
        sharing pipeline produces (``POST /api/sharing/acl/agent/{id}``). A
        listing that only knew ``public`` showed such an account nothing at all,
        while opening the same agent directly answered 200.

        ``exclude_user_id`` drops the viewer's own rows: the endpoint composes
        "mine ∪ visible" and an owned agent is listed once.
        """
        role, unit_keys = self._acl.scope_for_user(user_id)
        allowed = self._allowed_agent_ids(user_id=user_id, role=role, unit_keys=unit_keys)
        if not allowed:
            return []
        sql = (
            "SELECT * FROM agents WHERE enabled = 1 "
            f"AND agent_id IN ({sql_in_placeholders(len(allowed))})"
        )
        params: list[object] = [*sorted(allowed)]
        if exclude_user_id is not None:
            sql += " AND user_id != ?"
            params.append(exclude_user_id)
        sql += " ORDER BY created_at ASC, id ASC"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, AgentRow)

    def _allowed_agent_ids(
        self, *, user_id: int, role: str, unit_keys: Collection[str]
    ) -> set[str]:
        """Agent ids the actor's entries allow, under both names of the resource."""
        allowed = allowed_resource_ids(
            self._acl.list_for_type("agent"), user_id=user_id, role=role, unit_keys=unit_keys
        )
        allowed |= {
            feature_agent_id_for(feature_id)
            for feature_id in allowed_resource_ids(
                self._acl.list_for_type("feature"),
                user_id=user_id,
                role=role,
                unit_keys=unit_keys,
            )
        }
        return allowed

    def set_state(self, agent_id: str, state: str, *, error: str | None = None) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET last_state = ?, last_error = ?, updated_at = ? "
                "WHERE agent_id = ?",
                (state, error, now_ts(), agent_id),
            )

    def update_config(
        self,
        agent_id: str,
        *,
        name: str | None | object = UNSET,
        description: str | None | object = UNSET,
        persona_mbti: str | None | object = UNSET,
        default_model: str | None | object = UNSET,
        system_prompt: str | None | object = UNSET,
        config_json: str | None | object = UNSET,
        icon: str | None | object = UNSET,
        template_name: str | None | object = UNSET,
        color: str | None | object = UNSET,
        icon_name: str | None | object = UNSET,
        icon_url: str | None | object = UNSET,
        skill_package_ids: str | None | object = UNSET,
        published_expert_id: str | None | object = UNSET,
        welcome_message: str | None | object = UNSET,
        knowledge_base_ids: str | None | object = UNSET,
        mcp_servers: str | None | object = UNSET,
    ) -> None:
        fields, params = optional_updates(
            [
                ("name", name),
                ("description", description),
                ("persona_mbti", persona_mbti),
                ("default_model", default_model),
                ("system_prompt", system_prompt),
                ("config_json", config_json),
                ("icon", icon),
                ("template_name", template_name),
                ("color", color),
                ("icon_name", icon_name),
                ("icon_url", icon_url),
                ("skill_package_ids", skill_package_ids),
                ("published_expert_id", published_expert_id),
                ("welcome_message", welcome_message),
                ("knowledge_base_ids", knowledge_base_ids),
                ("mcp_servers", mcp_servers),
            ]
        )
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(now_ts())
        params.append(agent_id)
        with self._db.transaction() as conn:
            conn.execute(f"UPDATE agents SET {', '.join(fields)} WHERE agent_id = ?", params)

    def delete(self, agent_id: str) -> None:
        with self._db.transaction() as conn:
            row = conn.execute("SELECT kind FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
            conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
            self._acl.delete("agent", agent_id, conn=conn)
            # A feature's agent carries a second entry, filed under the feature's
            # own id (``resource_type='feature'``), and it names the same resource
            # — so it dies with the agent. Left behind it would decide a later
            # feature that reuses the id: its author would not own the row and the
            # audience the previous feature had would keep its access.
            if row is not None and is_feature_agent(str(row["kind"])):
                feature_id = feature_id_of_agent(agent_id)
                if feature_id is not None:
                    self._acl.delete("feature", feature_id, conn=conn)
