/**
 * The Personalization page's feature row — "which feature am I configuring?" —
 * and the agent bar that answers "whose agent is that, then?".
 *
 * Design 5.1 gives a feature an agent of its own, and this is how the expert
 * panels reach it: pick a feature and every panel on the page configures
 * ``feat-<feature_id>`` instead of the caller's own agent. Nothing global moves —
 * the active agent still drives chat and every other agent-scoped call, and the
 * ``AgentSelector`` would set it straight back anyway (it re-selects the first
 * owned expert whenever the current id is not one of the caller's).
 *
 * Two facts the bar refuses to blur:
 *   - The feature agent is **app-owned** (``user_id IS NULL``) and so is not in
 *     the caller's ``agents`` list: looking it up there answers ``undefined``,
 *     and "not in my list" would read on screen as "nothing configured". Its id
 *     comes from ``POST /features/{id}/agent`` (idempotent) and its runtime state
 *     from the agent status endpoint; a failure to get either is shown as itself.
 *   - While a feature is selected the ``AgentSelector`` is no longer the scope
 *     control, so it is replaced by what the panels are actually pointed at.
 */

import { Alert, Button, Select, Spin } from "antd";
import { Plus, RefreshCw, UserCog } from "lucide-react";
import { useTranslation } from "react-i18next";
import AgentSelector from "../../../../components/AgentSelector";
import styles from "../index.module.less";

export interface FeatureChoice {
  id: string;
  /** Already localized for the UI locale, as the catalog shows it. */
  label: string;
}

export default function FeatureScopeBar({
  features,
  featuresLoading,
  selected,
  onSelect,
  agentId,
  agentState,
  loading,
  /** Localized refusal from materializing (or reading) the agent, or ``null``. */
  failure,
  onRetry,
  retryLabel,
  onCreateFeature,
}: {
  /** Features this caller may configure. Empty hides the row — no permission,
   *  no control that could only be refused. */
  features: FeatureChoice[];
  featuresLoading: boolean;
  /** ``null`` = the caller's own agent, which is the page's ordinary mode. */
  selected: string | null;
  onSelect: (featureId: string | null) => void;
  /** The selected feature's agent, once the server has answered. */
  agentId: string | null;
  /** Its runtime state, as the server records it. */
  agentState: string | null;
  loading: boolean;
  failure: string | null;
  onRetry: () => void;
  /** The page's retry wording, so this bar and the panels read alike. */
  retryLabel: string;
  /** Where the "define one of your own" entry point goes — the catalog. */
  onCreateFeature: () => void;
}) {
  const { t } = useTranslation();
  const showPicker = featuresLoading || features.length > 0 || selected !== null;
  /**
   * Nothing to offer, and not because a call failed: every definition this
   * instance holds is one it ships. The row is not hidden for that — a page that
   * silently drops its scope control leaves the reader with no way to learn that
   * a feature *can* be configured, and none to go and make one.
   */
  const showNothingToConfigure = !featuresLoading && features.length === 0;

  return (
    <div className={styles.scope}>
      {showPicker && (
        <div className={styles.scopeRow}>
          <span className={styles.scopeLabel}>
            <UserCog size={13} strokeWidth={2} />
            {t("features.scopeCurrentFeature")}
          </span>
          <Select
            className={styles.scopeSelect}
            allowClear
            showSearch
            optionFilterProp="label"
            loading={featuresLoading}
            value={selected ?? undefined}
            placeholder={t("features.scopeFeaturePlaceholder")}
            onChange={(value?: string) => onSelect(value ?? null)}
            options={features.map((feature) => ({
              value: feature.id,
              label: feature.label,
            }))}
          />
        </div>
      )}

      {showNothingToConfigure && (
        <div className={styles.scopeEmpty}>
          <span>{t("features.scopeNoFeatures")}</span>
          <Button
            size="small"
            icon={<Plus size={13} />}
            onClick={onCreateFeature}
          >
            {t("features.scopeCreateFeature")}
          </Button>
        </div>
      )}

      <div className={styles.scopeAgent}>
        {selected === null ? (
          <AgentSelector />
        ) : (
          <div className={styles.scopeBound}>
            <span className={styles.scopeBoundLabel}>
              {t("features.scopeFeatureAgent")}
            </span>
            {loading ? (
              <Spin size="small" />
            ) : failure !== null ? (
              <Alert
                type="error"
                showIcon
                message={t("features.scopeFeatureAgentFailed")}
                description={failure}
                action={
                  <Button
                    size="small"
                    icon={<RefreshCw size={13} />}
                    onClick={onRetry}
                  >
                    {retryLabel}
                  </Button>
                }
              />
            ) : (
              <>
                <code className={styles.scopeAgentId}>{agentId}</code>
                <span className={styles.scopeAgentState} data-state={agentState}>
                  {agentState}
                </span>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
