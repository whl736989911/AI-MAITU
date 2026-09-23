import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Drawer, Empty, Input, Spin, Tag } from "antd";
import { message } from "@/utils/antdMessage";

import { CheckCircle, Download, RefreshCw, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  pluginsApi,
  type MarketPlugin,
  type MarketPluginDetail,
} from "../../../api/modules/plugins";
import { CardSkeleton } from "../../../components/Skeleton";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { apiErrorMessage } from "../../../utils/apiError";
import { pickLocale } from "../../../utils/localizedText";
import { normalizeUiLocale } from "../../../utils/localePrefs";
import { userCan } from "../../../utils/permissions";
import styles from "./index.module.less";
import { PluginIconView } from "./PluginIconView";

export interface PluginMarketPanelProps {
  /** Called after a successful install so the installed tab refetches. */
  onInstalled?: () => void;
  /** Changed by the installed tab so the market refetches install state. */
  refreshToken?: number;
}

/** Shipped plugin market: browse the wheel catalog and install in one click. */
export function PluginMarketPanel({
  onInstalled,
  refreshToken = 0,
}: PluginMarketPanelProps) {
  const { t, i18n } = useTranslation();
  const lang = normalizeUiLocale(i18n.language);
  const canInstall = userCan(useCurrentUser(), "plugins");
  const [items, setItems] = useState<MarketPlugin[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState<MarketPluginDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);

  const fetchMarket = useCallback(
    async (query: string, refresh = false) => {
      if (refresh) setRefreshing(true);
      try {
        const resp = await pluginsApi.marketList(query);
        setItems(resp?.items ?? []);
        setErrorMessage(null);
      } catch (err) {
        const msg = apiErrorMessage(err, t("plugins.marketLoadFailed"), t);
        setErrorMessage(msg);
        message.error(msg);
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [t],
  );

  // The catalog is local and tiny — filter server-side on each keystroke
  // instead of debouncing (no network round trip to hide).
  useEffect(() => {
    void fetchMarket(keyword);
  }, [keyword, fetchMarket, refreshToken]);

  const openDetail = useCallback(
    async (plugin: MarketPlugin) => {
      setSelected({ ...plugin, tools: [] });
      setInstallError(null);
      setDetailError(null);
      setDetailLoading(true);
      try {
        setSelected(await pluginsApi.marketGet(plugin.id));
      } catch (err) {
        // The card data is already on screen; only ``tools`` is missing. Say so
        // instead of leaving the section looking like a plugin that has none.
        const msg = apiErrorMessage(err, t("plugins.marketDetailFailed"), t);
        setDetailError(msg);
        message.error(msg);
      } finally {
        setDetailLoading(false);
      }
    },
    [t],
  );

  const handleInstall = useCallback(
    async (plugin: MarketPlugin) => {
      setInstallingId(plugin.id);
      setInstallError(null);
      try {
        await pluginsApi.marketInstall(plugin.id);
        message.success(
          t("plugins.marketInstallSuccess", { name: nameOf(plugin, lang) }),
        );
        await fetchMarket(keyword);
        onInstalled?.();
      } catch (err) {
        const msg = apiErrorMessage(err, t("plugins.marketInstallFailed"), t);
        setInstallError(msg);
        message.error(msg);
        return;
      } finally {
        setInstallingId(null);
      }
      // The install landed. Re-reading the open drawer is a separate concern:
      // a failure here must not be reported as a failed install.
      if (selected?.id === plugin.id) {
        try {
          setSelected(await pluginsApi.marketGet(plugin.id));
          setDetailError(null);
        } catch (err) {
          setDetailError(
            apiErrorMessage(err, t("plugins.marketDetailFailed"), t),
          );
        }
      }
    },
    [fetchMarket, keyword, lang, onInstalled, selected?.id, t],
  );

  const renderInstallButton = (plugin: MarketPlugin) => {
    if (plugin.enabled) {
      return (
        <span className={styles.marketInstalledLabel}>
          <CheckCircle size={14} />
          {t("plugins.marketEnabled")}
        </span>
      );
    }
    if (!canInstall) return null;
    return (
      <Button
        size="small"
        type="primary"
        icon={<Download size={14} />}
        loading={installingId === plugin.id}
        onClick={() => void handleInstall(plugin)}
      >
        {plugin.installed
          ? t("plugins.marketEnable")
          : t("plugins.marketInstall")}
      </Button>
    );
  };

  return (
    <div className={styles.panel}>
      <div className={styles.toolbar}>
        <div className={styles.toolbarLeft}>
          <span className={styles.toolbarCount}>
            {t("plugins.totalPlugins", { count: items.length })}
          </span>
        </div>
        <div className={styles.toolbarRight}>
          <Input
            className={styles.marketSearch}
            prefix={<Search size={14} />}
            allowClear
            value={keyword}
            placeholder={t("plugins.marketSearchPlaceholder")}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <Button
            icon={<RefreshCw size={14} />}
            loading={refreshing}
            onClick={() => void fetchMarket(keyword, true)}
          >
            {t("common.refresh")}
          </Button>
        </div>
      </div>

      {errorMessage && items.length > 0 && (
        <Alert
          className={styles.marketAlert}
          type="error"
          showIcon
          message={errorMessage}
          action={
            <Button
              size="small"
              onClick={() => void fetchMarket(keyword, true)}
            >
              {t("common.refresh")}
            </Button>
          }
        />
      )}

      {loading && items.length === 0 ? (
        <CardSkeleton count={3} />
      ) : items.length === 0 ? (
        errorMessage ? (
          <div className={styles.marketEmpty}>
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t("plugins.marketLoadFailed")}
            />
            <div className={styles.emptyHint}>{errorMessage}</div>
            <Button onClick={() => void fetchMarket(keyword, true)}>
              {t("common.refresh")}
            </Button>
          </div>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              keyword.trim()
                ? t("plugins.marketNoMatch", { keyword: keyword.trim() })
                : t("plugins.marketEmpty")
            }
          />
        )
      ) : (
        <div className={styles.cardGrid}>
          {items.map((plugin) => (
            <article
              key={plugin.id}
              className={`${styles.card} ${
                plugin.enabled ? "" : styles.cardDisabled
              }`}
            >
              <div className={styles.cardBody}>
                <div className={styles.cardTop}>
                  <PluginIconView
                    icon={plugin.icon}
                    size={48}
                    className={styles.cardIcon}
                  />
                  <div className={styles.cardTitleCol}>
                    <h3 className={styles.cardName}>{nameOf(plugin, lang)}</h3>
                    <div className={styles.cardChips}>
                      <Tag>{plugin.kind}</Tag>
                      <Tag>v{plugin.version}</Tag>
                      {plugin.installed && !plugin.enabled && (
                        <Tag color="default">{t("plugins.statusDisabled")}</Tag>
                      )}
                    </div>
                  </div>
                </div>
                <p className={styles.cardDesc}>
                  {descriptionOf(plugin, lang) || t("plugins.noDescription")}
                </p>
              </div>
              <div className={styles.cardFooter}>
                <button
                  className={styles.detailLink}
                  type="button"
                  onClick={() => void openDetail(plugin)}
                >
                  {t("plugins.viewDetails")}
                </button>
                <span className={styles.cardFooterSpacer} />
                {renderInstallButton(plugin)}
              </div>
            </article>
          ))}
        </div>
      )}

      <Drawer
        open={!!selected}
        width={520}
        destroyOnHidden
        onClose={() => setSelected(null)}
        title={selected ? nameOf(selected, lang) : ""}
        footer={
          selected ? (
            <div className={styles.marketDrawerFooter}>
              <Button onClick={() => setSelected(null)}>
                {t("common.close")}
              </Button>
              {renderInstallButton(selected)}
            </div>
          ) : null
        }
      >
        {selected && (
          <div className={styles.marketDrawerBody}>
            <div className={styles.marketChips}>
              <PluginIconView icon={selected.icon} size={56} />
              <div className={styles.cardChips}>
                <Tag>{selected.kind}</Tag>
                <Tag>v{selected.version}</Tag>
                {selected.installed && (
                  <Tag color={selected.enabled ? "success" : "default"}>
                    {selected.enabled
                      ? t("plugins.marketEnabled")
                      : t("plugins.statusDisabled")}
                  </Tag>
                )}
              </div>
            </div>
            {installError && (
              <Alert type="error" showIcon message={installError} />
            )}
            <p className={styles.cardDesc}>
              {descriptionOf(selected, lang) || t("plugins.noDescription")}
            </p>
            <div>
              <div className={styles.marketSectionTitle}>
                {t("plugins.marketRequires")}
              </div>
              {detailLoading ? (
                <Spin size="small" />
              ) : selected.requires.length > 0 ? (
                <div className={styles.marketChips}>
                  {selected.requires.map((requirement) => (
                    <Tag key={requirement}>{requirement}</Tag>
                  ))}
                </div>
              ) : (
                <div className={styles.toolsHint}>
                  {t("plugins.marketNoRequires")}
                </div>
              )}
            </div>
            <div>
              <div className={styles.marketSectionTitle}>
                {t("plugins.marketTools")}
              </div>
              {detailLoading ? (
                <Spin size="small" />
              ) : detailError ? (
                <Alert
                  type="error"
                  showIcon
                  message={detailError}
                  action={
                    <Button
                      size="small"
                      onClick={() => void openDetail(selected)}
                    >
                      {t("common.refresh")}
                    </Button>
                  }
                />
              ) : selected.tools && selected.tools.length > 0 ? (
                <div className={styles.marketTools}>
                  {selected.tools.map((tool) => (
                    <div key={tool.name} className={styles.marketToolRow}>
                      <span className={styles.tableMono}>{tool.name}</span>
                      <span className={styles.detailToolDesc}>
                        {tool.description}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className={styles.toolsHint}>
                  {selected.installed
                    ? t("plugins.noToolsListed")
                    : t("plugins.marketToolsPending")}
                </div>
              )}
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}

function nameOf(plugin: MarketPlugin, lang: "zh" | "en"): string {
  return pickLocale(plugin.name, lang) || plugin.id;
}

function descriptionOf(plugin: MarketPlugin, lang: "zh" | "en"): string {
  return pickLocale(plugin.description, lang) || "";
}
