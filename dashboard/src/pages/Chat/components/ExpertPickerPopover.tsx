import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { GraduationCap } from "lucide-react";
import SearchablePickerPanel, {
  pickerStyles,
} from "../../../components/ChatPicker/SearchablePickerPanel";
import { isFeatureAgent } from "../../../utils/agentKind";
import { indexAgentsByKind } from "../../../utils/agentKindCounts";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import {
  emptyAgentAccessFor,
  emptyAgentAccessPath,
} from "../utils/emptyAgentAccess";
import ExpertAgentAvatar, { type ChatAgentOption } from "./ExpertAgentAvatar";
import styles from "../index.module.less";

export type { ChatAgentOption };

interface ExpertPickerPopoverProps {
  agents: ChatAgentOption[];
  selectedAgentIds: string[];
  onSelect: (agent: ChatAgentOption) => void;
  onNavigateAway?: () => void;
}

export default function ExpertPickerPopover({
  agents,
  selectedAgentIds,
  onSelect,
  onNavigateAway,
}: ExpertPickerPopoverProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const currentUser = useCurrentUser();
  const access = emptyAgentAccessFor(currentUser);

  const filterFn = useCallback(
    (agent: ChatAgentOption, query: string) =>
      agent.name.toLowerCase().includes(query) ||
      agent.agent_id.toLowerCase().includes(query),
    [],
  );

  // Both kinds are offered here, and the picker says so: the features are the
  // labelled group, the experts are the group this picker has always listed and
  // keep its own words. Which kinds exist at all is this picker's own option
  // list's answer (``utils/agentKindCounts``) — a caller whose pickable agents
  // are all experts gets the picker unchanged, words included, and a caller who
  // can pick features is told which kinds the list they are searching holds.
  // The list arrives with each kind's rows contiguous (``pages/Chat/index.tsx``),
  // so the features are one group under one heading.
  const held = useMemo(() => indexAgentsByKind(agents).held, [agents]);
  const offersBothKinds = held.experts && held.features;
  const groupLabelFor = useCallback(
    (agent: ChatAgentOption) =>
      held.features && isFeatureAgent(agent)
        ? t("chat.featuresGroup", "功能")
        : null,
    [held.features, t],
  );

  return (
    <SearchablePickerPanel
      items={agents}
      filterFn={filterFn}
      searchPlaceholder={
        offersBothKinds
          ? t("chat.agentPickerSearch", "搜索专家与功能")
          : held.features
          ? t("chat.featurePickerSearch", "搜索功能")
          : t("chat.expertPickerSearch")
      }
      emptyMessage={
        offersBothKinds
          ? t("chat.agentPickerEmpty", "没有可选的专家或功能")
          : held.features
          ? t("chat.featurePickerEmpty", "没有可选的功能")
          : t("chat.expertPickerEmpty")
      }
      groupLabelFor={groupLabelFor}
      width="compact"
      footerIcon={<GraduationCap size={15} aria-hidden />}
      footerLabel={
        access === "none"
          ? undefined
          : t(
              access === "features"
                ? "chat.featurePickerManage"
                : access === "both"
                ? "chat.agentPickerManage"
                : "chat.expertPickerManage",
            )
      }
      onFooterClick={() => {
        if (access === "none") return;
        onNavigateAway?.();
        navigate(emptyAgentAccessPath(access));
      }}
      renderItem={(agent) => {
        const active = selectedAgentIds.includes(agent.agent_id);
        return (
          <button
            key={agent.agent_id}
            type="button"
            className={`${styles.skillPickerItem} ${
              active ? styles.expertPickerItemActive : ""
            }`}
            onClick={() => onSelect(agent)}
          >
            <ExpertAgentAvatar
              iconName={agent.icon_name}
              iconUrl={agent.icon_url}
              color={agent.color}
              size={32}
              iconSize={18}
            />
            <span className={styles.expertPickerItemText}>
              <span className={pickerStyles.itemName}>{agent.name}</span>
              {agent.is_shared && (
                <span className={styles.expertSharedBadge}>
                  {t("chat.expertSharedBadge", "共享")}
                </span>
              )}
            </span>
          </button>
        );
      }}
    />
  );
}
