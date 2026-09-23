import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Popconfirm,
  Segmented,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { message } from "@/utils/antdMessage";
import { ArrowRight, Check, Globe2, RefreshCw, Undo2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BRAND } from "../../../brand.generated";
import { request } from "../../../api/request";
import type {
  SharingAclEntry,
  SharingChange,
  SharingChangeStatus,
} from "../../../api/modules/sharing";
import { sharingApi } from "../../../api/modules/sharing";
import {
  adminOnlyErrorMessage,
  apiErrorMessage,
} from "../../../utils/apiError";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { isSystemAdmin } from "../../../utils/permissions";
import { EmptyState } from "../../../components/EmptyState";
import {
  GRANTEE_TYPE_LABEL_KEYS,
  RESOURCE_TYPE_LABEL_KEYS,
  STATUS_LABEL_KEYS,
  STATUS_NOTE_KEYS,
} from "../labels";
import {
  ImpactScopeTag,
  PermissionTag,
  StatusTag,
  VisibilityTag,
} from "./SharingTags";
import styles from "../index.module.less";

const { Text } = Typography;

const STATUS_ORDER: readonly SharingChangeStatus[] = [
  "pending_approval",
  "applied",
  "rejected",
  "rolled_back",
];

interface UserRow {
  id: number;
  username: string;
  display_name: string | null;
}

/**
 * One side of the before/after pair. Reach *and* level are both rendered: a
 * change moves either one, and a ``read`` → ``write`` escalation is invisible
 * to the reviewer when only the visibility is shown.
 */
function ChangeSide({
  label,
  entry,
}: {
  label: string;
  entry: SharingAclEntry;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.side}>
      <Text type="secondary" className={styles.sideLabel}>
        {label}
      </Text>
      <Space size={4} wrap>
        <VisibilityTag visibility={entry.visibility} />
        <PermissionTag permission={entry.permission} />
        {entry.unit_key && <Text code>{entry.unit_key}</Text>}
      </Space>
      <div className={styles.grantList}>
        {entry.grants.length === 0 ? (
          <Text type="secondary">{t("sharing.queue.noGrants")}</Text>
        ) : (
          entry.grants.map((grant) => (
            <Tag key={`${grant.grantee_type}:${grant.grantee_id}`}>
              {`${t(GRANTEE_TYPE_LABEL_KEYS[grant.grantee_type])}: ${
                grant.grantee_id
              }`}
            </Tag>
          ))
        )}
      </div>
    </div>
  );
}

/**
 * The approval queue: everything an administrator needs to judge one access
 * change without opening anything else — which resource, who asked, what the
 * visibility becomes, and why.
 *
 * A change that reaches the whole organization is the reason this screen
 * exists, so it is flagged in the brand accent and called out in words; a
 * parked change still answered 200, which is why the row's ``status`` and
 * ``applied`` — not an HTTP code — drive every label here.
 */
export default function ApprovalQueue() {
  const { t } = useTranslation();
  const currentUser = useCurrentUser();
  const admin = isSystemAdmin(currentUser);
  const timeZone = useServerTimezone();
  const [status, setStatus] = useState<SharingChangeStatus>("pending_approval");
  const [changes, setChanges] = useState<SharingChange[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rejectTarget, setRejectTarget] = useState<SharingChange | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [actorNames, setActorNames] = useState<Record<number, string>>({});

  // ``t`` is deliberately not a dependency: it changes identity on every render
  // in tests, and a reload only depends on the filter and the reload counter.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    sharingApi
      .listChanges(status)
      .then((res) => {
        if (cancelled) return;
        setChanges(res.changes ?? []);
        setLoadError(null);
      })
      .catch((error) => {
        if (cancelled) return;
        setChanges([]);
        setLoadError(error);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [status, reloadToken]);

  // Requester names are decoration: a missing user catalog falls back to the
  // bare id instead of blocking the queue.
  useEffect(() => {
    let cancelled = false;
    request<UserRow[]>("/users")
      .then((rows) => {
        if (cancelled) return;
        const names: Record<number, string> = {};
        for (const row of rows) {
          names[row.id] = row.display_name?.trim() || row.username;
        }
        setActorNames(names);
      })
      .catch(() => {
        if (!cancelled) setActorNames({});
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const decide = async (
    change: SharingChange,
    action: "approve" | "rollback",
  ) => {
    setBusyId(change.change_id);
    try {
      if (action === "approve") {
        await sharingApi.approveChange(change.change_id);
        message.success(t("sharing.queue.approved"));
      } else {
        await sharingApi.rollbackChange(change.change_id);
        message.success(t("sharing.queue.rolledBack"));
      }
      setReloadToken((n) => n + 1);
    } catch (error) {
      message.error(
        adminOnlyErrorMessage(
          error,
          t(
            action === "approve"
              ? "sharing.queue.approveFailed"
              : "sharing.queue.rollbackFailed",
          ),
          t,
        ),
      );
    } finally {
      setBusyId(null);
    }
  };

  const submitReject = async () => {
    if (!rejectTarget) return;
    const reason = rejectReason.trim();
    if (!reason) {
      message.error(t("sharing.queue.rejectReasonRequired"));
      return;
    }
    setBusyId(rejectTarget.change_id);
    try {
      await sharingApi.rejectChange(rejectTarget.change_id, reason);
      message.success(t("sharing.queue.rejected"));
      setRejectTarget(null);
      setRejectReason("");
      setReloadToken((n) => n + 1);
    } catch (error) {
      message.error(
        adminOnlyErrorMessage(error, t("sharing.queue.rejectFailed"), t),
      );
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className={styles.panel}>
      <div className={styles.panelToolbar}>
        <Segmented
          value={status}
          onChange={(value) => setStatus(value as SharingChangeStatus)}
          options={STATUS_ORDER.map((option) => ({
            value: option,
            label: t(STATUS_LABEL_KEYS[option]),
          }))}
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
          message={apiErrorMessage(loadError, t("sharing.queue.loadFailed"), t)}
        />
      )}

      {!admin && (
        <Text type="secondary" className={styles.panelIntro}>
          {t("sharing.queue.adminOnlyHint")}
        </Text>
      )}

      <Spin spinning={loading}>
        {changes.length === 0 && !loading && loadError === null ? (
          <EmptyState
            title={
              status === "pending_approval"
                ? t("sharing.queue.emptyPending")
                : t("sharing.queue.empty")
            }
            description={t("sharing.queue.emptyHint")}
          />
        ) : (
          <div className={styles.changeList}>
            {changes.map((change) => {
              const orgReaching = change.impact_scope === "org";
              const pending = change.status === "pending_approval";
              const resourceName =
                change.resource?.name ?? t("sharing.queue.unknownResource");
              return (
                <Card
                  key={change.change_id}
                  size="small"
                  className={styles.changeCard}
                  style={
                    orgReaching
                      ? { borderColor: BRAND.color.accent }
                      : undefined
                  }
                >
                  <div className={styles.changeHeader}>
                    <Space size={6} wrap>
                      <Tag>
                        {t(RESOURCE_TYPE_LABEL_KEYS[change.resource_type])}
                      </Tag>
                      <Text strong>{resourceName}</Text>
                      <Text code type="secondary">
                        {change.resource_id}
                      </Text>
                    </Space>
                    <Space size={6} wrap>
                      <StatusTag status={change.status} />
                      <ImpactScopeTag scope={change.impact_scope} />
                    </Space>
                  </div>

                  {orgReaching && (
                    <Alert
                      type="warning"
                      showIcon
                      icon={<Globe2 size={16} />}
                      className={styles.orgNotice}
                      message={t("sharing.queue.orgWarningTitle")}
                      description={t("sharing.queue.orgWarning")}
                    />
                  )}

                  <div className={styles.diffRow}>
                    <ChangeSide
                      label={t("sharing.queue.before")}
                      entry={change.before}
                    />
                    <ArrowRight size={16} className={styles.diffArrow} />
                    <ChangeSide
                      label={t("sharing.queue.after")}
                      entry={change.after}
                    />
                  </div>

                  <div className={styles.changeMeta}>
                    <span>
                      {t("sharing.queue.requestedBy")}{" "}
                      <Text strong>
                        {actorNames[change.actor_user_id] ??
                          t("sharing.queue.unknownActor", {
                            id: change.actor_user_id,
                          })}
                      </Text>
                    </span>
                    <span>
                      {t("sharing.queue.requestedAt")}{" "}
                      <Text strong>
                        {formatServerDateTime(change.created_at, timeZone)}
                      </Text>
                    </span>
                    <span>
                      {t("sharing.queue.version", {
                        from: change.from_version,
                        to: change.to_version,
                      })}
                    </span>
                    <span>
                      {t("sharing.queue.reason")}{" "}
                      <Text strong>
                        {change.reason?.trim() || t("sharing.queue.noReason")}
                      </Text>
                    </span>
                  </div>

                  <div className={styles.changeFooter}>
                    <Text type="secondary" className={styles.metaText}>
                      {t(STATUS_NOTE_KEYS[change.status])}
                    </Text>
                    <Space size={8}>
                      {pending && admin && (
                        <>
                          <Button
                            type="primary"
                            size="small"
                            icon={<Check size={14} />}
                            loading={busyId === change.change_id}
                            style={{
                              background: BRAND.color.accent,
                              borderColor: BRAND.color.accent,
                            }}
                            onClick={() => void decide(change, "approve")}
                          >
                            {t("sharing.queue.approve")}
                          </Button>
                          <Button
                            size="small"
                            danger
                            icon={<X size={14} />}
                            disabled={busyId === change.change_id}
                            onClick={() => {
                              setRejectTarget(change);
                              setRejectReason("");
                            }}
                          >
                            {t("sharing.queue.reject")}
                          </Button>
                        </>
                      )}
                      {change.status === "applied" && (
                        <Popconfirm
                          title={t("sharing.queue.rollbackConfirm")}
                          okText={t("common.confirm")}
                          cancelText={t("common.cancel")}
                          onConfirm={() => void decide(change, "rollback")}
                        >
                          <Button
                            size="small"
                            icon={<Undo2 size={14} />}
                            loading={busyId === change.change_id}
                          >
                            {t("sharing.queue.rollback")}
                          </Button>
                        </Popconfirm>
                      )}
                    </Space>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </Spin>

      <Modal
        title={t("sharing.queue.rejectTitle")}
        open={rejectTarget !== null}
        onCancel={() => setRejectTarget(null)}
        onOk={() => void submitReject()}
        okText={t("sharing.queue.reject")}
        cancelText={t("common.cancel")}
        okButtonProps={{ danger: true, loading: busyId !== null }}
        destroyOnHidden
      >
        <Text type="secondary" className={styles.modalHint}>
          {t("sharing.queue.rejectHint")}
        </Text>
        <Input.TextArea
          value={rejectReason}
          onChange={(event) => setRejectReason(event.target.value)}
          placeholder={t("sharing.queue.rejectReasonPlaceholder")}
          maxLength={200}
          showCount
          autoSize={{ minRows: 3, maxRows: 5 }}
        />
      </Modal>
    </div>
  );
}
