import { useMemo, useState } from "react";
import { Checkbox, Input } from "antd";
import { useTranslation } from "react-i18next";
import type { OctopAgent } from "../../context/AgentContext";
import styles from "./index.module.less";

interface Props {
  value?: string[];
  onChange?: (value: string[]) => void;
  experts: OctopAgent[];
}

export default function TeamMemberPicker({
  value = [],
  onChange,
  experts,
}: Props) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const candidates = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return experts
      .filter((agent) => agent.kind !== "team" && agent.kind !== "feature")
      .filter(
        (agent) =>
          !needle ||
          agent.name.toLocaleLowerCase().includes(needle) ||
          (agent.description ?? "").toLocaleLowerCase().includes(needle),
      );
  }, [experts, query]);
  const selected = useMemo(() => new Set(value), [value]);

  return (
    <div className={styles.picker}>
      <Input
        allowClear
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={t("teams.searchMembers", "Search experts")}
      />
      <div className={styles.pickerCount}>
        {t("teams.selectedCount", {
          count: value.length,
          defaultValue: "{{count}} selected",
        })}
      </div>
      <div className={styles.pickerList}>
        {candidates.map((agent) => (
          <label key={agent.agent_id} className={styles.pickerRow}>
            <Checkbox
              checked={selected.has(agent.agent_id)}
              onChange={(event) =>
                onChange?.(
                  event.target.checked
                    ? [...value, agent.agent_id]
                    : value.filter((id) => id !== agent.agent_id),
                )
              }
            />
            <span
              className={styles.memberDot}
              style={{ background: agent.color || "#5b8def" }}
            />
            <span className={styles.pickerName}>{agent.name}</span>
            <span className={styles.pickerDescription}>
              {agent.description || ""}
            </span>
          </label>
        ))}
        {!candidates.length && (
          <div className={styles.emptyPicker}>
            {t("teams.noMembers", "No available experts")}
          </div>
        )}
      </div>
    </div>
  );
}
