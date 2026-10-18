import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Modal } from "antd";
import { BookOpen, Power, RefreshCw } from "lucide-react";
import { updateApi } from "../../../api/modules/update";
import { useServiceRestartContext } from "../../../context/ServiceRestartContext";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useUpdateStatus } from "../../../hooks/useUpdateStatus";
import { storeUpdateStatus } from "../../../utils/updateStatusCache";
import { userCan } from "../../../utils/permissions";
import { apiErrorMessage } from "../../../utils/apiError";
import { TabPanelHeader } from "./TabPanelHeader";
import styles from "./UpdateConfig.module.less";

const ACTIVE_PHASES: Record<string, true> = {
  queued: true,
  downloading: true,
  validating: true,
  building: true,
  backing_up: true,
  stopping: true,
  installing: true,
  restarting: true,
  verifying: true,
};

export default function UpdateConfig() {
  const { t } = useTranslation();
  const { status, refreshStatus, isUnavailable } = useUpdateStatus();
  const user = useCurrentUser();
  const canUpdate = userCan(user, "update");
  const [checking, setChecking] = useState(false);
  const [submittingUpgrade, setSubmittingUpgrade] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const { restartPhase, isRestarting, requestRestart } =
    useServiceRestartContext();
  const phase = status?.upgrade_phase ?? "idle";
  const upgradeActive = ACTIVE_PHASES[phase] === true;
  const upgradeVersion = status?.latest_version;

  useEffect(() => {
    if (
      upgradeActive ||
      (status?.upgrade_phase === "succeeded" &&
        status.upgrade_target === status.current_version &&
        !status.has_update)
    ) {
      setActionError(null);
    }
  }, [
    status?.current_version,
    status?.has_update,
    status?.upgrade_phase,
    status?.upgrade_target,
    upgradeActive,
  ]);

  const checkForUpdates = async () => {
    setChecking(true);
    setActionError(null);
    try {
      storeUpdateStatus(await updateApi.checkForUpdates());
    } catch (error) {
      setActionError(
        apiErrorMessage(error, t("advancedSettings.update.checkFailed"), t),
      );
      void refreshStatus(true);
    } finally {
      setChecking(false);
    }
  };

  const submitUpgrade = async () => {
    if (!upgradeVersion) return;
    setSubmittingUpgrade(true);
    setActionError(null);
    try {
      storeUpdateStatus(await updateApi.upgrade(upgradeVersion));
    } catch (error) {
      setActionError(
        apiErrorMessage(error, t("advancedSettings.update.upgradeFailed"), t),
      );
      void refreshStatus(true);
    } finally {
      setSubmittingUpgrade(false);
    }
  };

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
            <div className={styles.versionCard}>
              <span className={styles.versionLabel}>
                {t("advancedSettings.update.latestVersion")}
              </span>
              <span className={styles.versionValue}>
                {status?.latest_version ??
                  t("advancedSettings.update.notChecked")}
              </span>
            </div>
          </div>
          {status?.release_notes && (
            <div className={styles.releaseNotes}>
              <h4>{t("advancedSettings.update.releaseNotes")}</h4>
              <pre>{status.release_notes}</pre>
            </div>
          )}
          {status?.checked_at && (
            <p className={styles.checkedAt}>
              {t("advancedSettings.update.checkedAt", {
                date: new Date(status.checked_at).toLocaleString(),
              })}
            </p>
          )}
          {status?.center_configured === false && (
            <p className={styles.statusMessage}>
              {t("advancedSettings.update.centerUnavailable")}
            </p>
          )}
          {status?.install_available === false && (
            <p className={styles.statusMessage}>
              {t("advancedSettings.update.installerUnavailable")}
            </p>
          )}
          {(actionError || status?.error) && (
            <p className={styles.errorMessage} role="alert">
              {actionError ?? status?.error}
            </p>
          )}
          {isUnavailable && !upgradeActive && (
            <p className={styles.statusMessage} role="status">
              {t("advancedSettings.update.statusUnavailable")}
            </p>
          )}
          {upgradeActive && (
            <p
              className={styles.statusMessage}
              role="status"
              aria-live="polite"
            >
              {t(`advancedSettings.update.phase.${phase}`, {
                version: status?.upgrade_target ?? upgradeVersion ?? "",
              })}
              {isUnavailable && ` ${t("advancedSettings.update.reconnecting")}`}
            </p>
          )}
          {phase === "failed" && (
            <p className={styles.errorMessage} role="alert">
              {status?.upgrade_error
                ? t("advancedSettings.update.upgradeError", {
                    error: status.upgrade_error,
                  })
                : t("advancedSettings.update.phase.failed")}
            </p>
          )}
          {status &&
            phase === "succeeded" &&
            status.upgrade_target === status.current_version &&
            !status.has_update &&
            !actionError && (
              <p className={styles.successMessage} role="status">
                {t("advancedSettings.update.upgradeSucceeded", {
                  version: status.current_version,
                })}
              </p>
            )}
          {canUpdate && (
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => void checkForUpdates()}
                disabled={checking || upgradeActive}
              >
                <RefreshCw size={14} />
                {checking
                  ? t("advancedSettings.update.checking")
                  : t("advancedSettings.update.checkNow")}
              </button>
              {status?.has_update && (
                <button
                  type="button"
                  className={styles.btnPrimary}
                  disabled={
                    !status.install_available ||
                    !upgradeVersion ||
                    upgradeActive ||
                    submittingUpgrade
                  }
                  onClick={() =>
                    Modal.confirm({
                      title: t("advancedSettings.update.upgradeConfirmTitle"),
                      content: t(
                        "advancedSettings.update.upgradeConfirmContent",
                        {
                          version: upgradeVersion,
                        },
                      ),
                      okText: t("advancedSettings.update.upgradeNow"),
                      cancelText: t("advancedSettings.update.restartCancel"),
                      onOk: submitUpgrade,
                    })
                  }
                >
                  <RefreshCw size={14} />
                  {submittingUpgrade
                    ? t("advancedSettings.update.upgrading")
                    : t("advancedSettings.update.upgradeTo", {
                        version: upgradeVersion,
                      })}
                </button>
              )}
              {status?.service_mode && (
                <button
                  type="button"
                  className={styles.btnSecondary}
                  onClick={requestRestart}
                  disabled={
                    restartPhase !== "idle" && restartPhase !== "timeout"
                  }
                >
                  <Power size={14} />
                  {isRestarting
                    ? t("advancedSettings.update.restarting")
                    : t("advancedSettings.update.restartServiceBtn")}
                </button>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
