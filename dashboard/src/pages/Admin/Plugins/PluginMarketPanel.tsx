import { useTranslation } from "react-i18next";
import { Store } from "lucide-react";
import { EmptyStateIcon } from "../../../components/EmptyState";
import styles from "./index.module.less";

/** Placeholder marketplace tab — under construction. */
export function PluginMarketPanel() {
  const { t } = useTranslation();
  return (
    <div className={styles.marketEmpty}>
      <EmptyStateIcon icon={Store} />
      <div className={styles.emptyTitle}>{t("plugins.marketTitle")}</div>
      <div className={styles.emptyHint}>{t("plugins.marketHint")}</div>
    </div>
  );
}
