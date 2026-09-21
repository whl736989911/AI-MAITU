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

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Alert, Button, Input, Popconfirm, Spin, Tabs } from "antd";
import {
  ArrowLeft,
  BookMarked,
  Check,
  Eye,
  Pencil,
  Play,
  Settings2,
  Sparkles,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  featuresApi,
  type Feature,
  type FeatureInputs,
  type FeatureOutputKind,
  type FeatureRunResponse,
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
import { useServerTimezone } from "../../hooks/useServerTimezone";
import CasesPanel from "./components/CasesPanel";
import FeatureSettingsDrawer from "./components/FeatureSettingsDrawer";
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
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

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

  // Keyed on the feature alone: a language switch must not throw away a run the
  // user already edited and finalized.
  useEffect(() => {
    setInputs({});
    setResult(null);
    setDraft("");
    setEditing(false);
    resetRun();
  }, [id, resetRun]);

  useEffect(() => {
    void load();
  }, [load]);

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

    setRunning(true);
    try {
      const run = await featuresApi.runFeature(
        feature.id,
        pruneBlankInputs(inputs),
      );
      resetRun();
      setDraft(run.output);
      setEditing(false);
      setResult(run);
    } catch (err) {
      message.error(apiErrorMessage(err, t("features.runFailed"), t));
    } finally {
      setRunning(false);
    }
  }, [feature, inputs, lang, resetRun, t]);

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

  // The finalized record belongs to the run on screen, not to the feature.
  const finalizedResult =
    result && learning.finalized?.task_id === result.task_id
      ? learning.finalized
      : null;
  const editable =
    canManage && metaReady && !meta.bundled_ids.includes(feature.id);

  return (
    <PageShell
      title={localizedText(feature.label, lang)}
      subtitle={localizedText(feature.description, lang)}
      actions={
        <div className={styles.pageActions}>
          <Button
            icon={<ArrowLeft size={14} />}
            onClick={() => navigate("/features")}
          >
            {t("features.backToList")}
          </Button>
          {editable && (
            <Button
              icon={<Settings2 size={14} />}
              onClick={() => setSettingsOpen(true)}
            >
              {t("features.settingsEdit")}
            </Button>
          )}
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
            {finalizedResult
              ? t("features.finalizedTitle")
              : t("features.draftTitle")}
          </div>
          {running ? (
            <div className={styles.loading}>
              <Spin />
            </div>
          ) : result ? (
            <DraftPane
              result={result}
              learning={learning}
              draft={draft}
              onDraftChange={setDraft}
              editing={editing}
              onEditingChange={setEditing}
            />
          ) : (
            <div className={styles.resultEmpty}>
              {t("features.resultEmpty")}
            </div>
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

      <FeatureSettingsDrawer
        open={settingsOpen}
        feature={feature}
        meta={meta}
        onClose={() => setSettingsOpen(false)}
        // The header, the form and the cards all read the definition: reload it.
        onSaved={() => {
          setSettingsOpen(false);
          void load();
        }}
        onDeleted={() => navigate("/features")}
      />
    </PageShell>
  );
}
