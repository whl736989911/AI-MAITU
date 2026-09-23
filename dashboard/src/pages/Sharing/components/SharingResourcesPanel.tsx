import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Input, List, Segmented, Spin, Typography } from "antd";
import { RefreshCw, Search, Share2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SharingResourceType } from "../../../api/modules/sharing";
import { EmptyState } from "../../../components/EmptyState";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { apiErrorMessage } from "../../../utils/apiError";
import { PERM, canAccessKeys } from "../../../utils/permissions";
import {
  RESOURCE_TYPE_LABEL_KEYS,
  SHARING_RESOURCE_TYPE_ORDER,
} from "../labels";
import {
  loadSharingResources,
  type SharingResourceOption,
} from "../resourceCatalog";
import SharingSettingsDrawer from "./SharingSettingsDrawer";
import styles from "../index.module.less";

const { Text } = Typography;

/**
 * Module keys each catalog is drawn from. ``null`` means the list needs no
 * module key at all — ``GET /agents`` is every signed-in account's own list, and
 * a feature is an agent row (``kind === "feature"``) it already carries.
 *
 * Every other catalog is a module's own list endpoint, so a type whose key the
 * caller does not hold can only answer 403: offering it would draw a tab whose
 * only possible content is an error (design §2.3, 不能…查看该类型).
 */
const RESOURCE_TYPE_PERMISSION_KEYS: Record<
  SharingResourceType,
  readonly string[] | null
> = {
  knowledge_base: PERM.knowledgeBasesPage,
  agent: null,
  connector: PERM.connectors,
  feature: null,
};

/**
 * The sharing-settings entry point: pick a resource, then open its access
 * drawer. Knowledge bases come first — that is the surface this page exists to
 * give an entry to — and the other three ACL resource types follow.
 */
export default function SharingResourcesPanel() {
  const { t } = useTranslation();
  const user = useCurrentUser();
  // The types this account can actually list. ``null`` means AuthGuard's
  // ``/auth/me`` has not landed yet: guessing from an absent account would hide
  // a tab the caller may well hold, and the panel is rendered under the page's
  // own guard, so the full order is what an unloaded user sees.
  const offeredTypes = useMemo(
    () =>
      user === null
        ? SHARING_RESOURCE_TYPE_ORDER
        : SHARING_RESOURCE_TYPE_ORDER.filter((type) => {
            const keys = RESOURCE_TYPE_PERMISSION_KEYS[type];
            return keys === null || canAccessKeys(user, keys);
          }),
    [user],
  );
  // The caller's pick, or the first type they may list ("agent" needs no key, so
  // the fallback is never empty).
  const [requestedType, setRequestedType] =
    useState<SharingResourceType | null>(null);
  const resourceType = requestedType ?? offeredTypes[0];
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
            setRequestedType(value as SharingResourceType);
          }}
          options={offeredTypes.map((type) => ({
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
