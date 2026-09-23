import { Spin, Tabs } from "antd";
import { ListChecks, Share2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import ForbiddenPage from "../../components/ForbiddenPage";
import TabLabel from "../../components/TabLabel";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import PageShell from "../../layouts/PageShell";
import { userCan } from "../../utils/permissions";
import ApprovalQueue from "./components/ApprovalQueue";
import SharingResourcesPanel from "./components/SharingResourcesPanel";
import styles from "./index.module.less";

type SharingTabKey = "queue" | "settings";

/**
 * Sharing governance (``/sharing``): the approval queue that decides org-wide
 * access changes, plus the per-resource sharing settings that raise them.
 *
 * Every ``/api/sharing`` endpoint is gated on the ``users`` module permission
 * (approving and rejecting are admin-only on top of that), so the page carries
 * the same gate in front of its content and the sidebar hides the entry
 * without it.
 */
export default function SharingPage() {
  const { t } = useTranslation();
  const user = useCurrentUser();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab: SharingTabKey =
    searchParams.get("tab") === "settings" ? "settings" : "queue";

  if (user === null) {
    return (
      <div className={styles.loading}>
        <Spin size="large" />
      </div>
    );
  }
  if (!userCan(user, "users")) return <ForbiddenPage />;

  const selectTab = (key: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("tab", key === "settings" ? "settings" : "queue");
    setSearchParams(next, { replace: true });
  };

  return (
    <PageShell.FillTabs
      title={t("pageShell.sharing.title")}
      subtitle={t("pageShell.sharing.subtitle")}
    >
      <Tabs
        activeKey={activeTab}
        onChange={selectTab}
        items={[
          {
            key: "queue",
            label: (
              <TabLabel icon={ListChecks}>{t("sharing.tabQueue")}</TabLabel>
            ),
            children: <ApprovalQueue />,
          },
          {
            key: "settings",
            label: (
              <TabLabel icon={Share2}>{t("sharing.tabSettings")}</TabLabel>
            ),
            children: <SharingResourcesPanel />,
          },
        ]}
      />
    </PageShell.FillTabs>
  );
}
