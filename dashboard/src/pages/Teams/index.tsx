import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Spin,
  Tag,
  Typography,
} from "antd";
import { MessageSquare, Pencil, Plus, Trash2, Users } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { message } from "@/utils/antdMessage";
import {
  teamsApi,
  type TeamRecord,
  type TeamWriteBody,
} from "../../api/modules/teams";
import { useAgent, type OctopAgent } from "../../context/AgentContext";
import TeamMemberPicker from "./TeamMemberPicker";
import styles from "./index.module.less";

type TeamForm = {
  name: string;
  description?: string;
  default_model?: string;
  welcome_message?: string;
  member_ids: string[];
};

export default function TeamsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { agents, refresh: refreshAgents } = useAgent();
  const experts = useMemo(
    () =>
      agents.filter(
        (agent) => agent.kind !== "team" && agent.kind !== "feature",
      ),
    [agents],
  );
  const [teams, setTeams] = useState<TeamRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<TeamRecord | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<TeamForm>();

  const reloadTeams = useCallback(async () => {
    setLoading(true);
    try {
      setTeams(await teamsApi.list());
    } catch {
      message.error(t("teams.loadFailed", "Unable to load teams"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void reloadTeams();
  }, [reloadTeams]);

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue({
      name: "",
      description: "",
      default_model: "",
      welcome_message: "",
      member_ids: [],
    });
    setDrawerOpen(true);
  };

  const openEdit = async (team: TeamRecord) => {
    setEditing(team);
    setDrawerOpen(true);
    try {
      const current = await teamsApi.get(team.team_id);
      setEditing(current);
      form.setFieldsValue({
        name: current.name,
        description: current.description ?? "",
        default_model: current.default_model ?? "",
        welcome_message: current.welcome_message ?? "",
        member_ids: current.members.map((member) => member.agent_id),
      });
    } catch {
      setDrawerOpen(false);
      message.error(t("teams.loadFailed", "Unable to load team"));
    }
  };

  const save = async () => {
    const values = await form.validateFields();
    if (values.member_ids.length < 2) {
      message.error(t("teams.membersMin", "Choose at least two experts"));
      return;
    }
    const body: TeamWriteBody = {
      name: values.name.trim(),
      description: values.description?.trim() || null,
      default_model: values.default_model?.trim() || null,
      welcome_message: values.welcome_message?.trim() || null,
      member_ids: values.member_ids,
    };
    setSaving(true);
    try {
      const updated = editing
        ? await teamsApi.update(editing.team_id, body)
        : await teamsApi.create({
            ...body,
            name: body.name!,
            member_ids: body.member_ids!,
          });
      setTeams((items) =>
        editing
          ? items.map((item) =>
              item.team_id === updated.team_id ? updated : item,
            )
          : [...items, updated],
      );
      setDrawerOpen(false);
      message.success(
        editing
          ? t("teams.updated", "Team updated")
          : t("teams.created", "Team created"),
      );
      await refreshAgents({ silent: true });
    } catch (error) {
      const text =
        error instanceof Error
          ? error.message
          : t("teams.saveFailed", "Unable to save team");
      message.error(text);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (team: TeamRecord) => {
    try {
      await teamsApi.remove(team.team_id);
      setTeams((items) =>
        items.filter((item) => item.team_id !== team.team_id),
      );
      await refreshAgents({ silent: true });
      message.success(t("teams.deleted", "Team deleted"));
    } catch (error) {
      message.error(
        error instanceof Error
          ? error.message
          : t("teams.deleteFailed", "Unable to delete team"),
      );
    }
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    setEditing(null);
  };

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <Typography.Title level={2}>
            {t("teams.title", "Teams")}
          </Typography.Title>
          <p>
            {t(
              "teams.description",
              "Combine experts under a host that organizes work and presents one conversation.",
            )}
          </p>
        </div>
        <Button type="primary" icon={<Plus size={16} />} onClick={openCreate}>
          {t("teams.create", "Create team")}
        </Button>
      </header>
      {loading ? (
        <div className={styles.loading}>
          <Spin />
        </div>
      ) : teams.length ? (
        <div className={styles.grid}>
          {teams.map((team) => (
            <Card key={team.team_id} className={styles.teamCard}>
              <div className={styles.cardHeader}>
                <div className={styles.teamIcon}>
                  <Users size={21} />
                </div>
                <div className={styles.titleBlock}>
                  <Typography.Title level={4}>{team.name}</Typography.Title>
                  <Tag color={team.state === "running" ? "green" : "default"}>
                    {team.state || "unknown"}
                  </Tag>
                </div>
              </div>
              {team.description && (
                <p className={styles.teamDescription}>{team.description}</p>
              )}
              <div className={styles.rosterHeader}>
                {t("teams.members", "Members")} · {team.members.length}
              </div>
              <div className={styles.roster}>
                {team.members.map((member) => (
                  <span
                    className={styles.memberChip}
                    key={member.agent_id}
                    title={member.name}
                  >
                    <span
                      className={styles.memberDot}
                      style={{ background: member.color || "#5b8def" }}
                    />
                    {member.name}
                  </span>
                ))}
              </div>
              <div className={styles.actions}>
                <Button
                  icon={<MessageSquare size={15} />}
                  onClick={() => navigate(`/chat/${team.agent_id}`)}
                >
                  {t("teams.openChat", "Open chat")}
                </Button>
                <Button
                  aria-label={t("common.edit", "Edit")}
                  icon={<Pencil size={15} />}
                  onClick={() => void openEdit(team)}
                />
                <Popconfirm
                  title={t("teams.confirmDelete", {
                    name: team.name,
                    defaultValue: `Delete ${team.name}?`,
                  })}
                  onConfirm={() => void remove(team)}
                >
                  <Button
                    danger
                    aria-label={t("common.delete", "Delete")}
                    icon={<Trash2 size={15} />}
                  />
                </Popconfirm>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <div className={styles.empty}>
          <Users size={34} />
          <h3>{t("teams.emptyTitle", "No teams yet")}</h3>
          <p>
            {t(
              "teams.emptyDescription",
              "Create a team and assign at least two experts.",
            )}
          </p>
          <Button type="primary" onClick={openCreate}>
            {t("teams.create", "Create team")}
          </Button>
        </div>
      )}

      <Drawer
        title={
          editing
            ? t("teams.editTitle", "Edit team")
            : t("teams.createTitle", "Create team")
        }
        open={drawerOpen}
        onClose={closeDrawer}
        width={560}
        destroyOnHidden
        footer={
          <div className={styles.drawerFooter}>
            <Button onClick={closeDrawer}>
              {t("common.cancel", "Cancel")}
            </Button>
            <Button type="primary" loading={saving} onClick={() => void save()}>
              {t("common.save", "Save")}
            </Button>
          </div>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label={t("teams.name", "Name")}
            rules={[{ required: true, whitespace: true }]}
          >
            <Input maxLength={120} />
          </Form.Item>
          <Form.Item
            name="description"
            label={t("teams.teamDescription", "Description")}
          >
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          <Form.Item
            name="default_model"
            label={t("teams.model", "Default model")}
          >
            <Input placeholder={t("teams.modelAuto", "Automatic")} />
          </Form.Item>
          <Form.Item
            name="welcome_message"
            label={t("teams.welcome", "Welcome message")}
          >
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="member_ids" label={t("teams.members", "Members")}>
            <TeamMemberPicker experts={experts} />
          </Form.Item>
          <p className={styles.hint}>
            {t(
              "teams.membersHint",
              "The host plans and delegates; members perform the specialist work.",
            )}
          </p>
        </Form>
      </Drawer>
    </main>
  );
}

export type { OctopAgent };
