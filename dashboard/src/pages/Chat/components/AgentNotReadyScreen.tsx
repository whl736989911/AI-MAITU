import { Button, Result, Spin } from "antd";
import { Bot, Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { EmptyStateIcon } from "../../../components/EmptyState";
import type { OctopAgent } from "../../../context/AgentContext";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import {
  emptyAgentAccessFor,
  emptyAgentAccessKey,
  emptyAgentAccessPath,
} from "../utils/emptyAgentAccess";
import {
  formatAgentError,
  isAgentModelConfigError,
} from "../../../utils/agentError";
import styles from "../index.module.less";

interface AgentNotReadyScreenProps {
  agent: OctopAgent | null;
  noAgents?: boolean;
  loading?: boolean;
}

export default function AgentNotReadyScreen({
  agent,
  noAgents = false,
  loading = false,
}: AgentNotReadyScreenProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const currentUser = useCurrentUser();

  const emptyStateVariant = emptyAgentAccessFor(currentUser);
  const emptyStateKey = emptyAgentAccessKey(emptyStateVariant);

  if (loading) {
    return (
      <div className={styles.agentNotReady}>
        <Spin />
      </div>
    );
  }

  if (noAgents) {
    return (
      <div className={styles.noAgentsEmpty}>
        <div className={styles.noAgentsEmptyInner}>
          <div className={styles.noAgentsEmptyIcon}>
            <EmptyStateIcon icon={Bot} />
          </div>
          <h1 className={styles.noAgentsEmptyTitle}>
            {t(`chat.noAgents${emptyStateKey}Title`)}
          </h1>
          <p className={styles.noAgentsEmptyHint}>
            {t(`chat.noAgents${emptyStateKey}Hint`)}
          </p>
          {emptyStateVariant !== "none" ? (
            <Button
              type="primary"
              size="large"
              onClick={() => navigate(emptyAgentAccessPath(emptyStateVariant))}
            >
              {t(`chat.noAgents${emptyStateKey}Action`)}
            </Button>
          ) : null}
        </div>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className={styles.agentNotReady}>
        <Result status="info" title={t("chat.pickAgent")} />
      </div>
    );
  }

  const state = agent.state;
  const errorText = formatAgentError(agent.last_error, t);
  const isModelError = isAgentModelConfigError(agent.last_error);

  const suffix = emptyStateVariant === "experts" ? "" : emptyStateKey;
  let titleKey = `agentNotRunning${suffix}`;
  let hintKey = `agentNotRunning${suffix}Hint`;

  if (state === "failed") {
    titleKey = `agentFailed${suffix}`;
    hintKey = `agentFailed${suffix}Hint`;
  } else if (state === "starting" || state === "stopping") {
    titleKey = `agentStarting${suffix}`;
    hintKey = `agentStarting${suffix}Hint`;
  }
  const title = t(`chat.${titleKey}`);
  const subTitle =
    state === "failed"
      ? errorText || t(`chat.${hintKey}`)
      : t(`chat.${hintKey}`);

  return (
    <div className={styles.agentNotReady}>
      <Result
        status={
          state === "failed"
            ? "error"
            : state === "stopped" || state === "created"
            ? "warning"
            : "info"
        }
        title={title}
        subTitle={subTitle}
        extra={
          isModelError ? (
            <Button
              type="primary"
              icon={<Settings size={14} />}
              onClick={() => navigate("/admin/models")}
            >
              {t("modelConfig.configureButton")}
            </Button>
          ) : emptyStateVariant === "none" ? null : (
            <Button
              type="primary"
              onClick={() => navigate(emptyAgentAccessPath(emptyStateVariant))}
            >
              {t(
                emptyStateVariant === "features"
                  ? "chat.goToFeatures"
                  : emptyStateVariant === "both"
                  ? "chat.goToAgents"
                  : "chat.goToExperts",
              )}
            </Button>
          )
        }
      />
    </div>
  );
}
