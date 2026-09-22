/**
 * Feature catalog — every ``feature.json`` on disk, grouped by organization
 * unit. Cards are the expert-template card, tinted with the feature's own
 * colour, and are the entry point into the schema-driven run form.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Spin } from "antd";
import { Plus, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  featuresApi,
  type FeatureSummary,
  type FeatureUnit,
} from "../../api/modules/features";
import { BRAND, brandName } from "../../brand.generated";
import { EmptyState } from "../../components/EmptyState";
import PageShell from "../../layouts/PageShell";
import { apiErrorMessage } from "../../utils/apiError";
import { normalizeUiLocale } from "../../utils/localePrefs";
import { isSystemAdmin } from "../../utils/permissions";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { TemplateCard } from "../Experts/components/ExpertCard";
import { FeatureIcon } from "./components/FeatureIcon";
import FeatureCreateDrawer from "./components/FeatureCreateDrawer";
import { localizedText } from "./components/SchemaForm";
import { useFeatureMeta } from "./components/useFeatureMeta";
import styles from "./index.module.less";

interface FeatureGroup {
  key: string;
  items: FeatureSummary[];
}

/** Group features by ``unit``, ordered by the API's unit list then first sight. */
function groupByUnit(
  features: FeatureSummary[],
  units: FeatureUnit[],
): FeatureGroup[] {
  const byUnit = new Map<string, FeatureSummary[]>();
  for (const feature of features) {
    const bucket = byUnit.get(feature.unit);
    if (bucket) bucket.push(feature);
    else byUnit.set(feature.unit, [feature]);
  }

  const groups: FeatureGroup[] = [];
  for (const unit of units) {
    const items = byUnit.get(unit.key);
    if (items) {
      groups.push({ key: unit.key, items });
      byUnit.delete(unit.key);
    }
  }
  for (const [key, items] of byUnit) groups.push({ key, items });
  return groups;
}

export default function FeaturesPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const lang = normalizeUiLocale(i18n.language);
  // Defining a feature is an administrator's job; everyone else just runs them,
  // so the entry point is absent rather than present-and-refused. It also waits
  // for the format choices — an editor without them would open on empty pickers.
  const canManage = isSystemAdmin(useCurrentUser());
  const { meta, ready: metaReady } = useFeatureMeta(canManage);

  const [features, setFeatures] = useState<FeatureSummary[]>([]);
  const [units, setUnits] = useState<FeatureUnit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await featuresApi.listFeatures();
      setFeatures(data.features ?? []);
      setUnits(data.units ?? []);
      setError(null);
    } catch (err) {
      setFeatures([]);
      setUnits([]);
      setError(apiErrorMessage(err, t("features.loadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const groups = useMemo(() => groupByUnit(features, units), [features, units]);

  return (
    <PageShell
      title={t("features.title")}
      subtitle={t("features.subtitle")}
      actions={
        <div className={styles.pageActions}>
          {canManage && metaReady && (
            <Button
              type="primary"
              icon={<Plus size={14} />}
              onClick={() => setSettingsOpen(true)}
            >
              {t("features.settingsNew")}
            </Button>
          )}
          <Button
            icon={<RefreshCw size={14} />}
            loading={loading}
            onClick={() => void load()}
          >
            {t("common.refresh")}
          </Button>
        </div>
      }
    >
      {loading && features.length === 0 && (
        <div className={styles.loading}>
          <Spin />
        </div>
      )}

      {!loading && error && (
        <EmptyState
          variant="error"
          title={t("features.loadFailed")}
          description={error}
          actionLabel={t("common.refresh")}
          onAction={() => void load()}
        />
      )}

      {!loading && !error && groups.length === 0 && (
        <EmptyState
          title={t("features.empty")}
          description={t("features.emptyHint", {
            brand: brandName(i18n.language),
          })}
        />
      )}

      {groups.map((group) => (
        <section className={styles.group} key={group.key}>
          <div className={styles.groupHead}>
            <h2 className={styles.groupTitle}>{group.key}</h2>
            <span className={styles.groupCount}>{group.items.length}</span>
          </div>
          <div className={styles.grid}>
            {group.items.map((feature) => (
              <TemplateCard
                key={feature.id}
                title={localizedText(feature.label, lang)}
                description={localizedText(feature.description, lang)}
                accent={feature.color || BRAND.color.accent}
                accentVar="--feature-tint"
                renderIcon={(size) => (
                  <FeatureIcon name={feature.icon_name} size={size} />
                )}
                onClick={() => navigate(`/features/${feature.id}`)}
              />
            ))}
          </div>
        </section>
      ))}

      <FeatureCreateDrawer
        open={settingsOpen}
        meta={meta}
        onClose={() => setSettingsOpen(false)}
        // The drawer holds the essentials; everything else about the definition
        // is on the settings page, which is where a new author goes next.
        onCreated={(featureId) => {
          setSettingsOpen(false);
          navigate(`/features/${featureId}/settings`);
        }}
      />
    </PageShell>
  );
}
