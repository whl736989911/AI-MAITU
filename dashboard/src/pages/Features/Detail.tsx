/**
 * Feature run page — schema-driven form on the left, the draft → final handoff
 * on the right. Submitting posts the inputs to ``/api/features/{id}/run`` and
 * renders whatever the agent produced according to the feature's ``output.kind``.
 *
 * The right pane is the entry point of the self-improvement loop: a run is a
 * *draft* until the person who started it edits it into the answer they would
 * actually ship and finalizes it. That finalize is what the rules and cases
 * below are induced from, so the pane is editable by default and becomes
 * read-only — and promotable — once the call is made.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { Alert, Button, Input, Popconfirm, Spin, Tabs } from "antd";
import {
  ArrowLeft,
  BookMarked,
  Check,
  Eye,
  ListOrdered,
  Pencil,
  Play,
  Settings2,
  Sparkles,
  UserCog,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  featuresApi,
  type Feature,
  type FeatureInputs,
  type FeatureOutputKind,
  type FeatureRunResponse,
  type FeatureStepRun,
} from "../../api/modules/features";
import { EmptyState } from "../../components/EmptyState";
import LazyMarkdown from "../../components/Markdown/LazyMarkdown";
import TabLabel from "../../components/TabLabel";
import PageShell from "../../layouts/PageShell";
import { message } from "@/utils/antdMessage";
import { apiErrorMessage } from "../../utils/apiError";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import { normalizeUiLocale } from "../../utils/localePrefs";
import { isSystemAdmin } from "../../utils/permissions";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { usePathTabs } from "../../hooks/usePathTabs";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import CasesPanel from "./components/CasesPanel";
import FeatureDefinitionEditor, {
  type FeatureSettingsTab,
} from "./components/FeatureDefinitionEditor";
import FeaturePersonalizationPanel from "./components/FeaturePersonalizationPanel";
import FeatureRunSteps from "./components/FeatureRunSteps";
import RulesPanel from "./components/RulesPanel";
import SchemaForm, {
  fieldLabel,
  localizedText,
  missingRequiredFields,
  pruneBlankInputs,
} from "./components/SchemaForm";
import {
  useFeatureLearning,
  type FeatureLearning,
} from "./components/useFeatureLearning";
import { useFeatureMeta } from "./components/useFeatureMeta";
import styles from "./index.module.less";

/**
 * What a feature's own page holds, in the order the tabs are offered.
 *
 * The first two are a feature's own: running it is what it is for, and the
 * personalization set is the experts' panels pointed at *its* agent. The last two
 * are the definition itself — the part no expert has an equivalent of — and they
 * are offered exactly to a caller who may write it.
 */
export type FeatureTab = "run" | "personalization" | FeatureSettingsTab;

const FEATURE_TABS = [
  "run",
  "personalization",
  "definition",
  "steps",
] as const satisfies readonly FeatureTab[];

/** Where the page opens, and where a tab nobody here may show falls back to. */
const DEFAULT_TAB: FeatureTab = "run";

const TAB_ICONS = {
  run: Play,
  personalization: UserCog,
  definition: Settings2,
  steps: ListOrdered,
} as const;

const TAB_LABEL_KEYS: Record<FeatureTab, string> = {
  run: "features.tabRun",
  personalization: "features.tabPersonalization",
  definition: "features.settingsTabDefinition",
  steps: "features.settingsTabSteps",
};

/** The agent's text, rendered the way its ``output.kind`` promises. */
function OutputBody({ text, kind }: { text: string; kind: FeatureOutputKind }) {
  if (kind === "markdown") {
    return <LazyMarkdown content={text} className={styles.resultMarkdown} />;
  }
  if (kind === "json") {
    let pretty = text;
    try {
      pretty = JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      pretty = text;
    }
    return <pre className={styles.resultPre}>{pretty}</pre>;
  }
  return <pre className={styles.resultPre}>{text}</pre>;
}

function TaskIdNote({ taskId }: { taskId: string }) {
  const { t } = useTranslation();
  return (
    <div className={styles.resultMeta}>
      {t("features.taskId")} <code>{taskId}</code>
    </div>
  );
}

interface DraftPaneProps {
  result: FeatureRunResponse;
  learning: FeatureLearning;
  draft: string;
  onDraftChange: (value: string) => void;
  editing: boolean;
  onEditingChange: (value: boolean) => void;
}

/**
 * Draft → final → case. A run can only settle once: after finalize (or after a
 * 409 telling us somebody else got there first) the text turns read-only, since
 * the stored final is the learning signal and hides behind no endpoint we could
 * re-read.
 */
function DraftPane({
  result,
  learning,
  draft,
  onDraftChange,
  editing,
  onEditingChange,
}: DraftPaneProps) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [note, setNote] = useState("");

  // A note describes one run; carrying it into the next one would mislabel it.
  useEffect(() => setNote(""), [result.task_id]);

  const finalized =
    learning.finalized?.task_id === result.task_id ? learning.finalized : null;
  const conflict = finalized ? null : learning.finalizeConflict;
  const editable = !finalized && !conflict;
  const promoted = learning.promotedTaskId === result.task_id;

  return (
    <>
      <div className={styles.draftHead}>
        <span
          className={`${styles.draftBadge} ${
            finalized ? styles.draftBadgeFinal : ""
          }`}
        >
          {finalized ? t("features.finalizedBadge") : t("features.draftBadge")}
        </span>
        {finalized?.finalized_at != null && (
          <span className={styles.draftStamp}>
            {t("features.finalizedAt", {
              time: formatServerDateTime(finalized.finalized_at, timeZone),
            })}
          </span>
        )}
        {editable && (
          <div className={styles.draftTools}>
            {editing ? (
              <Button
                size="small"
                icon={<Eye size={13} />}
                onClick={() => onEditingChange(false)}
              >
                {t("features.preview")}
              </Button>
            ) : (
              <Button
                size="small"
                icon={<Pencil size={13} />}
                onClick={() => onEditingChange(true)}
              >
                {t("features.edit")}
              </Button>
            )}
          </div>
        )}
      </div>

      {conflict && (
        <Alert
          type="warning"
          showIcon
          message={t("features.finalizedConflictTitle")}
          description={conflict}
        />
      )}

      {editable && editing ? (
        <Input.TextArea
          className={styles.draftEditor}
          value={draft}
          autoSize={{ minRows: 12, maxRows: 28 }}
          onChange={(event) => onDraftChange(event.target.value)}
        />
      ) : (
        <OutputBody
          text={finalized ? finalized.final : draft}
          kind={result.output_kind}
        />
      )}

      <TaskIdNote taskId={result.task_id} />

      {editable && (
        <div className={styles.draftFooter}>
          <Button
            type="primary"
            icon={<Check size={14} />}
            loading={learning.finalizing}
            disabled={draft.trim() === ""}
            onClick={() => void learning.finalize(result.task_id, draft)}
          >
            {t("features.finalize")}
          </Button>
          <span className={styles.draftHint}>{t("features.finalizeHint")}</span>
        </div>
      )}

      {finalized && (
        <div className={styles.draftFooter}>
          <Popconfirm
            title={t("features.promoteConfirmTitle")}
            description={
              <div className={styles.promoteConfirm}>
                <div>{t("features.promoteConfirmDesc")}</div>
                <Input.TextArea
                  value={note}
                  placeholder={t("features.promoteNotePlaceholder")}
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  onChange={(event) => setNote(event.target.value)}
                />
              </div>
            }
            okText={t("features.promote")}
            cancelText={t("common.cancel")}
            onConfirm={() => void learning.promote(result.task_id, note)}
          >
            <Button
              icon={<BookMarked size={14} />}
              loading={learning.promoting}
              disabled={promoted}
            >
              {promoted ? t("features.promotedBadge") : t("features.promote")}
            </Button>
          </Popconfirm>
          <span className={styles.draftHint}>{t("features.promoteHint")}</span>
        </div>
      )}

      {finalized && (
        <div className={styles.draftDone}>{t("features.finalizeDoneHint")}</div>
      )}
    </>
  );
}

export default function FeatureDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const lang = normalizeUiLocale(i18n.language);
  // Writing a definition is an administrator's job; a bundled feature belongs to
  // the app, so neither surface offers the settings entry point.
  const canManage = isSystemAdmin(useCurrentUser());
  const { meta, ready: metaReady } = useFeatureMeta(canManage);

  const learning = useFeatureLearning(id);
  const { resetRun } = learning;

  const [feature, setFeature] = useState<Feature | null>(null);
  const [inputs, setInputs] = useState<FeatureInputs>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<FeatureRunResponse | null>(null);
  /**
   * The run as the step engine reports it — present only for a definition that
   * declares steps. It is the same run across a gate: approving continues it.
   */
  const [stepRun, setStepRun] = useState<FeatureStepRun | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      setFeature(await featuresApi.getFeature(id));
      setError(null);
    } catch (err) {
      setFeature(null);
      setError(apiErrorMessage(err, t("features.loadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [id, t]);

  /**
   * Re-read the definition after a write. Kept apart from the first load: this
   * one must not blank the page, because the page's surfaces stay mounted — the
   * header follows the server's copy while the tabs stay where the author left
   * them, and the editor re-seeds from the copy that came back.
   */
  const reload = useCallback(async () => {
    if (!id) return;
    try {
      setFeature(await featuresApi.getFeature(id));
    } catch (err) {
      message.error(apiErrorMessage(err, t("features.loadFailed"), t));
    }
  }, [id, t]);

  // Keyed on the feature alone: a language switch must not throw away a run the
  // user already edited and finalized.
  useEffect(() => {
    setInputs({});
    setResult(null);
    setStepRun(null);
    setDraft("");
    setEditing(false);
    resetRun();
  }, [id, resetRun]);

  useEffect(() => {
    void load();
  }, [load]);

  const bundled =
    feature !== null && meta.bundled_ids.includes(feature.id);

  /**
   * Whether this caller may configure the definition: write it, and give it an
   * agent of its own to personalize. Both are administrator calls and neither is
   * accepted for a definition the app ships, so one answer covers the three tabs
   * they gate.
   *
   * Every term is load-bearing. ``metaReady`` is what says a definition is not
   * one the app ships — an unanswered ``bundled_ids`` would offer exactly the
   * ones the server refuses. ``feature !== null`` is what keeps that true while
   * the definition itself is still in flight: the bundled list cannot be read
   * against a definition that has not arrived, and acting on the half-answer
   * would open a feature's agent — a write — before knowing whether it may.
   */
  const mayConfigure = canManage && metaReady && feature !== null && !bundled;

  const isTabAllowed = useCallback(
    (tab: FeatureTab) => tab === DEFAULT_TAB || mayConfigure,
    [mayConfigure],
  );

  const { activeTab, handleTabChange, isMounted } = usePathTabs<FeatureTab>({
    basePath: `/features/${id ?? ""}`,
    tabs: FEATURE_TABS,
    storageKey: "octop:features:tab",
    defaultTab: DEFAULT_TAB,
    isAllowed: isTabAllowed,
  });

  const pathTabs = useMemo(
    () => ({
      value: activeTab,
      onChange: handleTabChange,
      options: FEATURE_TABS.filter((value) => isTabAllowed(value)).map(
        (value) => {
          const Icon = TAB_ICONS[value];
          return {
            value,
            label: t(TAB_LABEL_KEYS[value]),
            icon: <Icon size={14} strokeWidth={2} />,
          };
        },
      ),
    }),
    [activeTab, handleTabChange, isTabAllowed, t],
  );

  const handleRun = useCallback(async () => {
    if (!feature) return;
    const missing = missingRequiredFields(feature.input_schema, inputs);
    if (missing.length > 0) {
      const name = missing[0];
      message.error(
        t("features.requiredMissing", {
          field: fieldLabel(name, feature.input_schema.properties[name], lang),
        }),
      );
      return;
    }

    // The button is on every tab, and the run it starts is on one of them.
    handleTabChange(DEFAULT_TAB);
    setRunning(true);
    try {
      const run = await featuresApi.runFeature(
        feature.id,
        pruneBlankInputs(inputs),
      );
      resetRun();
      setEditing(false);
      if ("steps" in run) {
        // A definition with steps answers with the run itself. Nothing is
        // delivered yet at a gate, so the pane shows the run and not a draft.
        setStepRun(run);
        setDraft(run.output ?? "");
        setResult(
          run.output === null
            ? null
            : {
                task_id: run.task_id,
                output: run.output,
                output_kind: run.output_kind,
              },
        );
      } else {
        setStepRun(null);
        setDraft(run.output);
        setResult(run);
      }
    } catch (err) {
      message.error(apiErrorMessage(err, t("features.runFailed"), t));
    } finally {
      setRunning(false);
    }
  }, [feature, handleTabChange, inputs, lang, resetRun, t]);

  /**
   * Approving a gate, rewinding, or refreshing re-answers with the same run in a
   * new state. Its text follows: a run that delivered nothing yet keeps the pane
   * on the step view rather than showing an empty draft.
   */
  const handleRunChange = useCallback((next: FeatureStepRun) => {
    setStepRun(next);
    setDraft(next.output ?? "");
    setResult(
      next.output === null
        ? null
        : {
            task_id: next.task_id,
            output: next.output,
            output_kind: next.output_kind,
          },
    );
  }, []);

  if (loading) {
    return (
      <PageShell title={t("features.title")}>
        <div className={styles.loading}>
          <Spin />
        </div>
      </PageShell>
    );
  }

  if (!feature) {
    return (
      <PageShell title={t("features.title")}>
        <EmptyState
          variant="error"
          title={t("features.loadFailed")}
          description={error ?? t("features.notFound")}
          actionLabel={t("features.backToList")}
          onAction={() => navigate("/features")}
        />
      </PageShell>
    );
  }

  // A tab this caller may not show has nothing to render: a URL that names one
  // lands on the run surface instead of on an empty pane. Waited for the answer
  // on purpose, so a writer's own bookmark is never bounced by a slow load.
  if ((metaReady || !canManage) && !mayConfigure && activeTab !== DEFAULT_TAB) {
    return <Navigate to={`/features/${feature.id}/${DEFAULT_TAB}`} replace />;
  }

  // The finalized record belongs to the run on screen, not to the feature.
  const finalizedResult =
    result && learning.finalized?.task_id === result.task_id
      ? learning.finalized
      : null;
  // A correction at a rerun is a write to the step that produced the artifact,
  // and only a step declaring ``allow_edit`` accepts one.
  const editableSteps = Object.fromEntries(
    (feature.steps ?? []).map((step) => [step.id, step.allow_edit === true]),
  );

  return (
    <PageShell
      title={localizedText(feature.label, lang)}
      subtitle={localizedText(feature.description, lang)}
      pathTabs={pathTabs}
      fill
      actions={
        <div className={styles.pageActions}>
          <Button
            icon={<ArrowLeft size={14} />}
            onClick={() => navigate("/features")}
          >
            {t("features.backToList")}
          </Button>
          <Button
            type="primary"
            icon={<Play size={14} />}
            loading={running}
            onClick={() => void handleRun()}
          >
            {t("features.run")}
          </Button>
        </div>
      }
    >
      <div className={styles.featurePanels}>
        {isMounted(DEFAULT_TAB) && (
          <div
            className={styles.featurePanel}
            style={{ display: activeTab === DEFAULT_TAB ? "flex" : "none" }}
            aria-hidden={activeTab !== DEFAULT_TAB}
          >
            <div className={styles.featureScroll}>
              {bundled && (
                // A definition the app ships has no agent of its own and no
                // editable copy: said here rather than as a control that could
                // only be refused. Defining one of its own is the way to get one
                // that can be configured.
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message={t("features.bundledNotice")}
                />
              )}
              <div className={styles.detail}>
                <section className={styles.pane}>
                  <div className={styles.paneTitle}>{t("features.formTitle")}</div>
                  <SchemaForm
                    schema={feature.input_schema}
                    uiSchema={feature.ui_schema}
                    value={inputs}
                    onChange={setInputs}
                    disabled={running}
                  />
                </section>
                <section className={styles.pane}>
                  <div className={styles.paneTitle}>
                    {stepRun && !result
                      ? t("features.runStepsTitle")
                      : finalizedResult
                        ? t("features.finalizedTitle")
                        : t("features.draftTitle")}
                  </div>
                  {running ? (
                    <div className={styles.loading}>
                      <Spin />
                    </div>
                  ) : (
                    <>
                      {stepRun && (
                        <FeatureRunSteps
                          featureId={feature.id}
                          run={stepRun}
                          onRunChange={handleRunChange}
                          editableSteps={editableSteps}
                        />
                      )}
                      {result ? (
                        <DraftPane
                          result={result}
                          learning={learning}
                          draft={draft}
                          onDraftChange={setDraft}
                          editing={editing}
                          onEditingChange={setEditing}
                        />
                      ) : (
                        !stepRun && (
                          <div className={styles.resultEmpty}>
                            {t("features.resultEmpty")}
                          </div>
                        )
                      )}
                    </>
                  )}
                </section>
              </div>

              <Tabs
                className={styles.learningTabs}
                items={[
                  {
                    key: "rules",
                    label: (
                      <TabLabel icon={Sparkles}>
                        <span className={styles.tabText}>
                          {t("features.rulesTab")}
                          {learning.pendingRules > 0 && (
                            <span className={styles.tabCount}>
                              {learning.pendingRules}
                            </span>
                          )}
                        </span>
                      </TabLabel>
                    ),
                    children: <RulesPanel learning={learning} />,
                  },
                  {
                    key: "cases",
                    label: (
                      <TabLabel icon={BookMarked}>
                        <span className={styles.tabText}>
                          {t("features.casesTab")}
                          {learning.cases.length > 0 && (
                            <span className={styles.tabCount}>
                              {learning.cases.length}
                            </span>
                          )}
                        </span>
                      </TabLabel>
                    ),
                    children: (
                      <CasesPanel
                        learning={learning}
                        outputKind={feature.output_kind}
                      />
                    ),
                  },
                ]}
              />
            </div>
          </div>
        )}

        {mayConfigure && isMounted("personalization") && (
          <div
            className={styles.featurePanel}
            style={{
              display: activeTab === "personalization" ? "flex" : "none",
            }}
            aria-hidden={activeTab !== "personalization"}
          >
            <FeaturePersonalizationPanel
              featureId={feature.id}
              canWrite={mayConfigure}
            />
          </div>
        )}

        {mayConfigure && (isMounted("definition") || isMounted("steps")) && (
          <div
            className={styles.featurePanel}
            style={{
              display:
                activeTab === "definition" || activeTab === "steps"
                  ? "flex"
                  : "none",
            }}
            aria-hidden={activeTab !== "definition" && activeTab !== "steps"}
          >
            <FeatureDefinitionEditor
              feature={feature}
              meta={meta}
              activeTab={activeTab === "steps" ? "steps" : "definition"}
              isMounted={isMounted}
              onSaved={() => void reload()}
              // The definition is gone: the catalog is the only place left.
              onDeleted={() => navigate("/features")}
              // Leaving is discarding: the editor stays mounted while the
              // feature's other surfaces are on screen, so the server's own copy
              // has to be put back in the form by hand.
              onCancel={() => {
                void reload();
                handleTabChange(DEFAULT_TAB);
              }}
            />
          </div>
        )}
      </div>
    </PageShell>
  );
}
