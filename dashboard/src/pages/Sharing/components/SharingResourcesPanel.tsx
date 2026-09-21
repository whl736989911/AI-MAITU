import { useEffect, useState } from "react";
import { Alert, Button, Input, List, Segmented, Spin, Typography } from "antd";
import { RefreshCw, Search, Share2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SharingResourceType } from "../../../api/modules/sharing";
import { EmptyState } from "../../../components/EmptyState";
import { apiErrorMessage } from "../../../utils/apiError";
import { RESOURCE_TYPE_LABEL_KEYS, SHARING_RESOURCE_TYPE_ORDER } from "../labels";
import {
  loadSharingResources,
  type SharingResourceOption,
} from "../resourceCatalog";
import SharingSettingsDrawer from "./SharingSettingsDrawer";
import styles from "../index.module.less";

const { Text } = Typography;

/**
 * The sharing-settings entry point: pick a resource, then open its access
 * drawer. Knowledge bases come first — that is the surface this page exists to
 * give an entry to — and the other three ACL resource types follow.
 */
export default function SharingResourcesPanel() {
  const { t } = useTranslation();
  // Knowledge bases first: the drawer is the sharing entry those rows lack.
  const [resourceType, setResourceType] =
    useState<SharingResourceType>("knowledge_base");
  const [options, setOptions] = useState<SharingResourceOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<SharingResourceOption | null>(null);

  // ``t`` is deliberately not a dependency: it changes identity on every render
  // in tests, and the catalog only depends on the type and the reload counter.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadSharingResources(resourceType)
      .then((rows) => {
        if (cancelled) return;
        setOptions(rows);
        setLoadError(null);
      })
      .catch((error) => {
        if (cancelled) return;
        setOptions([]);
        setLoadError(error);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [resourceType, reloadToken]);

  const needle = query.trim().toLowerCase();
  const filtered = needle
    ? options.filter(
        (option) =>
          option.name.toLowerCase().includes(needle) ||
          (option.hint ?? "").toLowerCase().includes(needle),
      )
    : options;

  return (
    <div className={styles.panel}>
      <Text type="secondary" className={styles.panelIntro}>
        {t("sharing.settings.intro")}
      </Text>

      <div className={styles.panelToolbar}>
        <Segmented
          value={resourceType}
          onChange={(value) => {
            setQuery("");
            setResourceType(value as SharingResourceType);
          }}
          options={SHARING_RESOURCE_TYPE_ORDER.map((type) => ({
            value: type,
            label: t(RESOURCE_TYPE_LABEL_KEYS[type]),
          }))}
        />
        <Input
          allowClear
          prefix={<Search size={14} />}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("sharing.settings.searchPlaceholder")}
          className={styles.search}
        />
        <Button
          icon={<RefreshCw size={14} />}
          onClick={() => setReloadToken((n) => n + 1)}
        >
          {t("common.refresh")}
        </Button>
      </div>

      {loadError !== null && (
        <Alert
          type="error"
          showIcon
          message={apiErrorMessage(
            loadError,
            t("sharing.settings.loadFailed"),
            t,
          )}
        />
      )}

      <Spin spinning={loading}>
        {filtered.length === 0 && !loading && loadError === null ? (
          <EmptyState
            title={t("sharing.settings.empty")}
            description={t("sharing.settings.emptyHint")}
          />
        ) : (
          <List
            className={styles.resourceList}
            dataSource={filtered}
            renderItem={(option) => (
              <List.Item
                key={option.id}
                actions={[
                  <Button
                    key="sharing"
                    size="small"
                    icon={<Share2 size={14} />}
                    onClick={() => setSelected(option)}
                  >
                    {t("sharing.settings.open")}
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={option.name}
                  description={option.hint ?? t("sharing.settings.noOwner")}
                />
              </List.Item>
            )}
          />
        )}
      </Spin>

      {selected && (
        <SharingSettingsDrawer
          open={selected !== null}
          onClose={() => setSelected(null)}
          resourceType={resourceType}
          resourceId={selected.id}
          resourceName={selected.name}
        />
      )}
    </div>
  );
}
