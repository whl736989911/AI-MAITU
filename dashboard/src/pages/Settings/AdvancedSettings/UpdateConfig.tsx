import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { BookOpen, Power, RefreshCw } from "lucide-react";
import { updateApi, type UpdateStatus } from "../../../api/modules/update";
import { useServiceRestartContext } from "../../../context/ServiceRestartContext";
import { TabPanelHeader } from "./TabPanelHeader";
import styles from "./UpdateConfig.module.less";

const REPO_URL = "https://github.com/whl736989911/AI-MAITU";

const UPGRADE_GUIDE_CODE = {
  source: `# In an existing checkout of ${REPO_URL}, after stopping the service:
git pull --ff-only
uv sync
cd dashboard && npm ci && npm run build && cd ..
uv run octop run --port 8088`,
  docker: `# In the existing checkout, rebuild and recreate with the same data volume
git pull --ff-only
docker compose -f docker/docker-compose.yml up -d --build`,
  restart: `# For a system service after the build completes
octop service restart`,
} as const;

type GuideMethodKey = keyof typeof UPGRADE_GUIDE_CODE;
const GUIDE_METHOD_ORDER: GuideMethodKey[] = ["source", "docker", "restart"];

function UpgradeGuide() {
  const { t } = useTranslation();

  return (
    <section
      className={`${styles.panel} ${styles.guide}`}
      aria-label={t("advancedSettings.update.guideTitle")}
    >
      <div className={styles.panelTitleRow}>
        <span className={styles.panelTitleIcon}>
          <BookOpen size={16} />
        </span>
        <h3 className={styles.panelTitle}>
          {t("advancedSettings.update.guideTitle")}
        </h3>
      </div>
      <p className={styles.panelDesc}>
        {t("advancedSettings.update.guideIntro")}{" "}
        <a href={REPO_URL} target="_blank" rel="noreferrer">
          {REPO_URL}
        </a>
      </p>
      <p className={styles.guideNote}>
        {t("advancedSettings.update.guideDataNote")}
      </p>
      <div className={styles.guideMethods}>
        {GUIDE_METHOD_ORDER.map((key) => (
          <div key={key} className={styles.guideMethod}>
            <p className={styles.guideMethodTitle}>
              {t(`advancedSettings.update.guide.${key}.title`)}
            </p>
            <p className={styles.guideMethodBody}>
              {t(`advancedSettings.update.guide.${key}.body`)}
            </p>
            <pre className={styles.guideCode}>{UPGRADE_GUIDE_CODE[key]}</pre>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function UpdateConfig() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const { restartPhase, isRestarting, requestRestart } =
    useServiceRestartContext();

  useEffect(() => {
    updateApi
      .getUpdateStatus()
      .then(setStatus)
      .catch(() => {});
  }, []);

  return (
    <div className={styles.container}>
      <TabPanelHeader
        icon={<RefreshCw size={22} />}
        title={t("advancedSettings.update.title")}
        description={t("advancedSettings.update.description")}
      />
      <div className={styles.layout}>
        <section
          className={styles.panel}
          aria-label={t("advancedSettings.update.panelTitle")}
        >
          <div className={styles.panelTitleRow}>
            <span className={styles.panelTitleIcon}>
              <BookOpen size={16} />
            </span>
            <h3 className={styles.panelTitle}>
              {t("advancedSettings.update.panelTitle")}
            </h3>
          </div>
          <p className={styles.panelDesc}>
            {t("advancedSettings.update.panelDesc")}
          </p>
          <div className={styles.versionGrid}>
            <div className={styles.versionCard}>
              <span className={styles.versionLabel}>
                {t("advancedSettings.update.currentVersion")}
              </span>
              <span className={styles.versionValue}>
                {status?.current_version ?? "—"}
              </span>
            </div>
          </div>
          {status?.service_mode && (
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={requestRestart}
                disabled={restartPhase !== "idle" && restartPhase !== "timeout"}
              >
                <Power size={14} />
                {isRestarting
                  ? t("advancedSettings.update.restarting")
                  : t("advancedSettings.update.restartServiceBtn")}
              </button>
            </div>
          )}
        </section>
        <UpgradeGuide />
      </div>
    </div>
  );
}
