/**
 * Feature settings — an independent route holding the whole definition, split
 * across URL tabs: ``/features/:id/settings/definition`` and
 * ``/features/:id/settings/steps``.
 *
 * The definition outgrew a drawer (nine blocks, and the ability to grow more),
 * so this is the same surface the rest of the app uses for a page that holds
 * several panels: ``usePathTabs`` for the URL-synced tab, and keep-alive panels
 * so switching tabs neither loses what was typed nor pays a remount.
 *
 * Three rules the surface obeys, unchanged from the drawer it replaces:
 *   - Only an administrator may write, and a bundled feature belongs to the app
 *     rather than to the workspace. Both cases hide the surface rather than
 *     offering a control that can only be refused: the entry point is absent,
 *     and reaching this URL directly lands back on the run page.
 *   - A refused write stays on screen, next to the fields that need fixing.
 *   - Saving is one commit for the whole definition, from whichever tab the
 *     author happens to be on — the panels are kept mounted for that reason.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { Form, Spin } from "antd";
import { ListOrdered, Settings2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  featuresApi,
  type Feature,
  type FeatureMeta,
} from "../../api/modules/features";
import { EmptyState } from "../../components/EmptyState";
import PageShell from "../../layouts/PageShell";
import { message } from "@/utils/antdMessage";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { usePathTabs } from "../../hooks/usePathTabs";
import { apiErrorMessage } from "../../utils/apiError";
import { normalizeUiLocale } from "../../utils/localePrefs";
import { isSystemAdmin } from "../../utils/permissions";
import { FeatureCapabilitySection } from "./components/FeatureCapabilityFields";
import { FeatureStepsSection } from "./components/FeatureStepsFields";
import {
  FeatureBasicsSection,
  FeatureDangerSection,
  FeatureInputFieldsSection,
  FeatureOutputSection,
  FeaturePermissionsSection,
  FeaturePromptSection,
  FeatureUnitSection,
  SaveErrorAlert,
  SettingsFooter,
} from "./components/FeatureSettingsSections";
import { localizedText } from "./components/SchemaForm";
import { useFeatureMeta } from "./components/useFeatureMeta";
import { useFeatureSettingsForm } from "./components/useFeatureSettingsForm";
import type { FeatureFormValues } from "./components/featureSettings";
import styles from "./index.module.less";

type FeatureSettingsTab = "definition" | "steps";

const FEATURE_SETTINGS_TABS = [
  "definition",
  "steps",
] as const satisfies readonly FeatureSettingsTab[];

const TAB_ICONS = {
  definition: Settings2,
  steps: ListOrdered,
} as const;

const TAB_LABEL_KEYS: Record<FeatureSettingsTab, string> = {
  definition: "features.settingsTabDefinition",
  steps: "features.settingsTabSteps",
};

/**
 * The editor itself. Only ever mounted for a definition this user may write, so
 * every gate below is a property of the page as a whole and not of a block.
 */
function FeatureSettingsEditor({
  feature,
  meta,
  onSaved,
  onDeleted,
}: {
  feature: Feature;
  meta: FeatureMeta;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = normalizeUiLocale(i18n.language);
  const navigate = useNavigate();
  const { form, saving, deleting, saveError, submit, remove } =
    useFeatureSettingsForm({ feature, meta, onSaved, onDeleted });

  const { activeTab, handleTabChange, isMounted } =
    usePathTabs<FeatureSettingsTab>({
      basePath: `/features/${feature.id}/settings`,
      tabs: FEATURE_SETTINGS_TABS,
      storageKey: "octop:features:settings:tab",
      defaultTab: "definition",
    });

  const pathTabs = useMemo(
    () => ({
      value: activeTab,
      onChange: handleTabChange,
      options: FEATURE_SETTINGS_TABS.map((value) => {
        const Icon = TAB_ICONS[value];
        return {
          value,
          label: t(TAB_LABEL_KEYS[value]),
          icon: <Icon size={14} strokeWidth={2} />,
        };
      }),
    }),
    [activeTab, handleTabChange, t],
  );

  return (
    <PageShell
      title={`${localizedText(feature.label, lang)} / ${t(
        "features.settingsEditTitle",
      )}`}
      subtitle={localizedText(feature.description, lang)}
      pathTabs={pathTabs}
      fill
    >
      <Form
        form={form}
        layout="vertical"
        className={`${styles.settingsForm} ${styles.settingsScroll}`}
        onFinish={() =>
          // The whole store, not just the registered fields. Two blocks are
          // collapsed by default (a collapsed antd ``Collapse`` does not mount
          // its content, so its fields are unregistered), and a tab nobody has
          // opened yet has never mounted its panel at all. Reading the store is
          // what makes one commit write the whole definition instead of
          // silently dropping the parts that were never on screen.
          void submit(form.getFieldsValue(true) as FeatureFormValues)
        }
      >
        <SaveErrorAlert error={saveError} />

        {isMounted("definition") && (
          <div
            className={styles.settingsPanel}
            style={{
              display: activeTab === "definition" ? "flex" : "none",
            }}
            aria-hidden={activeTab !== "definition"}
          >
            <FeatureBasicsSection creating={false} meta={meta} />
            <FeatureInputFieldsSection />
            <FeaturePromptSection />
            <FeatureCapabilitySection />
            <FeatureOutputSection meta={meta} />
            <FeatureUnitSection meta={meta} />
            <FeaturePermissionsSection meta={meta} />
            <FeatureDangerSection
              feature={feature}
              deleting={deleting}
              onDelete={() => void remove()}
            />
          </div>
        )}

        {isMounted("steps") && (
          <div
            className={styles.settingsPanel}
            style={{ display: activeTab === "steps" ? "flex" : "none" }}
            aria-hidden={activeTab !== "steps"}
          >
            <FeatureStepsSection />
          </div>
        )}
      </Form>

      <SettingsFooter
        className={styles.settingsFooter}
        saving={saving}
        onCancel={() => navigate(`/features/${feature.id}`)}
        onSubmit={() => form.submit()}
      />
    </PageShell>
  );
}

export default function FeatureSettingsPage() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  // Writing a definition is an administrator's job; a bundled feature belongs to
  // the app. Neither may open this surface, and neither is shown an entry point
  // that would be refused — a direct visit leaves on the run page.
  const canManage = isSystemAdmin(useCurrentUser());
  const { meta, ready: metaReady } = useFeatureMeta(canManage);

  const [feature, setFeature] = useState<Feature | null>(null);
  const [loading, setLoading] = useState(true);
  /** What the load failed with, kept raw and translated where it is shown. */
  const [loadFailure, setLoadFailure] = useState<unknown>(null);

  // Keyed on the id alone, and *not* on the translator: ``t`` is what the
  // failure is worded with, but making it a dependency would re-run this load —
  // and blank the editor it rendered — on every render the translator changes.
  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      setFeature(await featuresApi.getFeature(id));
      setLoadFailure(null);
    } catch (err) {
      setFeature(null);
      setLoadFailure(err);
    } finally {
      setLoading(false);
    }
  }, [id]);

  /**
   * Re-read the definition after a write. Kept apart from the first load: this
   * one must not blank the page, because the editor stays mounted — the header
   * follows the server's copy while the form keeps the tabs the author has open.
   */
  const reload = useCallback(async () => {
    if (!id) return;
    try {
      setFeature(await featuresApi.getFeature(id));
    } catch (err) {
      message.error(apiErrorMessage(err, t("features.loadFailed"), t));
    }
  }, [id, t]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!canManage || !id) {
    return <Navigate to={id ? `/features/${id}` : "/features"} replace />;
  }

  // ``bundled_ids`` is what says whether a definition may be edited at all, and
  // an unanswered list would offer to edit the ones the app ships.
  if (!metaReady || loading) {
    return (
      <PageShell title={t("features.settingsEditTitle")}>
        <div className={styles.loading}>
          <Spin />
        </div>
      </PageShell>
    );
  }

  if (meta.bundled_ids.includes(id)) {
    return <Navigate to={`/features/${id}`} replace />;
  }

  if (!feature) {
    return (
      <PageShell title={t("features.settingsEditTitle")}>
        <EmptyState
          variant="error"
          title={t("features.loadFailed")}
          description={
            loadFailure === null
              ? t("features.notFound")
              : apiErrorMessage(loadFailure, t("features.loadFailed"), t)
          }
          actionLabel={t("features.backToList")}
          onAction={() => navigate("/features")}
        />
      </PageShell>
    );
  }

  return (
    <FeatureSettingsEditor
      feature={feature}
      meta={meta}
      onSaved={() => void reload()}
      // The definition is gone: the catalog is the only place left to land.
      onDeleted={() => navigate("/features")}
    />
  );
}
