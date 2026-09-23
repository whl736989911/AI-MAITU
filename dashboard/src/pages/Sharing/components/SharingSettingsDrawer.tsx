import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Drawer,
  Divider,
  Input,
  Radio,
  Select,
  Space,
  Spin,
  Tooltip,
  Typography,
} from "antd";
import { message } from "@/utils/antdMessage";
import { Globe2, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BRAND } from "../../../brand.generated";
import type {
  SharingAclEntry,
  SharingChangeResult,
  SharingGranteeType,
  SharingGrant,
  SharingOrgUnit,
  SharingPermission,
  SharingResourceType,
  SharingVisibility,
} from "../../../api/modules/sharing";
import { sharingApi } from "../../../api/modules/sharing";
import { apiErrorMessage } from "../../../utils/apiError";
import { normalizeUiLocale } from "../../../utils/localePrefs";
import { pickLocale } from "../../../utils/localizedText";
import {
  GRANTEE_TYPE_LABEL_KEYS,
  PERMISSION_HINT_KEYS,
  PERMISSION_LABEL_KEYS,
  RESOURCE_TYPE_LABEL_KEYS,
  VISIBILITY_HINT_KEYS,
  VISIBILITY_LABEL_KEYS,
} from "../labels";
import { ImpactScopeTag, PermissionTag, VisibilityTag } from "./SharingTags";
import styles from "../index.module.less";

const { Text } = Typography;

const VISIBILITIES: readonly SharingVisibility[] = [
  "private",
  "unit",
  "public",
];
const GRANTEE_TYPES: readonly SharingGranteeType[] = ["user", "unit", "role"];
/** Read first: it is the default and the narrower of the two. */
const PERMISSIONS: readonly SharingPermission[] = ["read", "write"];

export interface SharingSettingsDrawerProps {
  open: boolean;
  onClose: () => void;
  resourceType: SharingResourceType;
  resourceId: string;
  resourceName: string;
  /** Fired after the backend accepted a change — applied or parked alike. */
  onSubmitted?: (result: SharingChangeResult) => void;
}

/**
 * Sharing settings for one resource: the visibility in force, plus the form
 * that changes it.
 *
 * The whole point of this drawer is that the requester knows the outcome
 * *before* and *after* submitting:
 * - a widening to ``public`` is flagged up front, because it cannot take
 *   effect on its own — an admin has to approve it;
 * - the outcome is read from ``status`` / ``applied``, never from the HTTP
 *   code: a parked change answers 200 exactly like an applied one.
 */
export default function SharingSettingsDrawer({
  open,
  onClose,
  resourceType,
  resourceId,
  resourceName,
  onSubmitted,
}: SharingSettingsDrawerProps) {
  const { t, i18n } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [entryError, setEntryError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [entry, setEntry] = useState<SharingAclEntry | null>(null);
  const [visibility, setVisibility] = useState<SharingVisibility>("private");
  const [permission, setPermission] = useState<SharingPermission>("read");
  const [unitKey, setUnitKey] = useState<string | null>(null);
  const [grants, setGrants] = useState<SharingGrant[]>([]);
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<SharingChangeResult | null>(null);
  const [orgUnits, setOrgUnits] = useState<SharingOrgUnit[] | null>(null);

  // ``t`` is deliberately not a dependency: it changes identity on every render
  // in tests, and the state read only depends on the resource being edited.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setResult(null);
    setReason("");
    sharingApi
      .getAcl(resourceType, resourceId)
      .then((res) => {
        if (cancelled) return;
        setEntry(res.entry);
        setVisibility(res.entry?.visibility ?? "private");
        setPermission(res.entry?.permission ?? "read");
        setUnitKey(res.entry?.unit_key ?? null);
        setGrants(res.entry?.grants ?? []);
        setEntryError(null);
      })
      .catch((error) => {
        if (cancelled) return;
        setEntryError(error);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, resourceType, resourceId]);

  // The unit catalog is only needed once the caller picks ``unit``.
  useEffect(() => {
    if (!open || visibility !== "unit" || orgUnits !== null) return;
    let cancelled = false;
    sharingApi
      .listOrgUnits()
      .then((units) => {
        if (!cancelled) setOrgUnits(units);
      })
      .catch(() => {
        if (!cancelled) setOrgUnits([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, visibility, orgUnits]);

  const locale = normalizeUiLocale(i18n.language);
  const unitOptions = useMemo(
    () =>
      (orgUnits ?? []).map((unit) => ({
        value: unit.key,
        label: pickLocale(unit.label, locale) || unit.key,
      })),
    [orgUnits, locale],
  );

  const nextGrants = grants
    .map((grant) => ({ ...grant, grantee_id: grant.grantee_id.trim() }))
    .filter((grant) => grant.grantee_id.length > 0);
  const orgReach = visibility === "public";

  const submit = async () => {
    setSaving(true);
    try {
      const outcome = await sharingApi.changeAcl(resourceType, resourceId, {
        visibility,
        unit_key: visibility === "unit" ? unitKey : null,
        permission,
        grants: nextGrants,
        reason: reason.trim() ? reason.trim() : null,
      });
      setResult(outcome);
      setEntry(outcome.entry);
      if (outcome.status === "pending_approval") {
        message.warning(t("sharing.settings.result.pending"));
      } else if (outcome.applied) {
        message.success(t("sharing.settings.result.applied"));
      } else {
        // ``applied: false`` on any other status means the state was replaced
        // by a later change; the queue shows what is in force.
        message.info(t("sharing.settings.result.superseded"));
      }
      onSubmitted?.(outcome);
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("sharing.settings.result.failed"), t),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer
      title={t("sharing.settings.drawerTitle", { name: resourceName })}
      placement="right"
      open={open}
      onClose={onClose}
      width={Math.min(
        560,
        typeof window !== "undefined" ? window.innerWidth - 24 : 560,
      )}
      destroyOnHidden
      styles={{ body: { paddingTop: 12 } }}
    >
      <Spin spinning={loading}>
        {entryError !== null && (
          <Alert
            type="error"
            showIcon
            className={styles.resultAlert}
            message={apiErrorMessage(
              entryError,
              t("sharing.settings.loadEntryFailed"),
              t,
            )}
          />
        )}

        <div className={styles.drawerSection}>
          <Text type="secondary">{t("sharing.settings.resourceType")}</Text>
          <div>
            <Text strong>{t(RESOURCE_TYPE_LABEL_KEYS[resourceType])}</Text>
          </div>
        </div>

        <div className={styles.drawerSection}>
          <Text type="secondary">
            {t("sharing.settings.currentVisibility")}
          </Text>
          <div className={styles.currentRow}>
            {entry ? (
              <>
                <VisibilityTag visibility={entry.visibility} />
                <PermissionTag permission={entry.permission} />
                <Text type="secondary" className={styles.metaText}>
                  {t("sharing.settings.version", { version: entry.version })}
                </Text>
                {entry.unit_key && (
                  <Text type="secondary" className={styles.metaText}>
                    {t("sharing.settings.unitSnapshot", {
                      unit: entry.unit_key,
                    })}
                  </Text>
                )}
              </>
            ) : (
              <Text type="secondary">{t("sharing.settings.notShared")}</Text>
            )}
          </div>
        </div>

        <Divider className={styles.drawerDivider} />

        {orgReach && (
          <Alert
            type="warning"
            showIcon
            icon={<Globe2 size={16} />}
            className={styles.orgNotice}
            message={t("sharing.settings.orgNoticeTitle")}
            description={t("sharing.settings.orgNotice")}
          />
        )}

        <div className={styles.drawerSection}>
          <Text strong>{t("sharing.settings.visibilityLabel")}</Text>
          <Radio.Group
            className={styles.visibilityGroup}
            value={visibility}
            onChange={(event) =>
              setVisibility(event.target.value as SharingVisibility)
            }
          >
            {VISIBILITIES.map((option) => (
              <Radio
                key={option}
                value={option}
                className={styles.visibilityOption}
              >
                <span className={styles.visibilityOptionTitle}>
                  {t(VISIBILITY_LABEL_KEYS[option])}
                </span>
                <span className={styles.visibilityOptionHint}>
                  {t(VISIBILITY_HINT_KEYS[option])}
                </span>
              </Radio>
            ))}
          </Radio.Group>
        </div>

        <div className={styles.drawerSection}>
          <Text strong>{t("sharing.settings.permissionLabel")}</Text>
          <Radio.Group
            className={styles.visibilityGroup}
            value={permission}
            onChange={(event) =>
              setPermission(event.target.value as SharingPermission)
            }
          >
            {PERMISSIONS.map((option) => (
              <Radio
                key={option}
                value={option}
                className={styles.visibilityOption}
              >
                <span className={styles.visibilityOptionTitle}>
                  {t(PERMISSION_LABEL_KEYS[option])}
                </span>
                <span className={styles.visibilityOptionHint}>
                  {t(PERMISSION_HINT_KEYS[option])}
                </span>
              </Radio>
            ))}
          </Radio.Group>
          <Text type="secondary" className={styles.metaText}>
            {t("sharing.settings.permissionHint")}
          </Text>
        </div>

        {visibility === "unit" && (
          <div className={styles.drawerSection}>
            <Text strong>{t("sharing.settings.unitLabel")}</Text>
            <Select
              allowClear
              value={unitKey ?? undefined}
              onChange={(value) => setUnitKey(value ?? null)}
              options={unitOptions}
              placeholder={t("sharing.settings.unitPlaceholder")}
              notFoundContent={t("sharing.settings.noUnits")}
              className={styles.fullWidth}
            />
            <Text type="secondary" className={styles.metaText}>
              {t("sharing.settings.unitHint")}
            </Text>
          </div>
        )}

        <div className={styles.drawerSection}>
          <Text strong>{t("sharing.settings.grantsTitle")}</Text>
          <Text type="secondary" className={styles.metaText}>
            {t("sharing.settings.grantsHint")}
          </Text>
          {grants.map((grant, index) => (
            <Space.Compact key={index} block className={styles.grantRow}>
              <Select
                value={grant.grantee_type}
                onChange={(value) =>
                  setGrants((current) =>
                    current.map((item, i) =>
                      i === index
                        ? { ...item, grantee_type: value as SharingGranteeType }
                        : item,
                    ),
                  )
                }
                options={GRANTEE_TYPES.map((kind) => ({
                  value: kind,
                  label: t(GRANTEE_TYPE_LABEL_KEYS[kind]),
                }))}
                className={styles.grantType}
              />
              <Input
                value={grant.grantee_id}
                onChange={(event) =>
                  setGrants((current) =>
                    current.map((item, i) =>
                      i === index
                        ? { ...item, grantee_id: event.target.value }
                        : item,
                    ),
                  )
                }
                placeholder={t("sharing.settings.granteePlaceholder")}
              />
              <Tooltip title={t("sharing.settings.removeGrant")}>
                <Button
                  icon={<Trash2 size={14} />}
                  onClick={() =>
                    setGrants((current) =>
                      current.filter((_, i) => i !== index),
                    )
                  }
                />
              </Tooltip>
            </Space.Compact>
          ))}
          <Button
            icon={<Plus size={14} />}
            onClick={() =>
              setGrants((current) => [
                ...current,
                { grantee_type: "user", grantee_id: "" },
              ])
            }
          >
            {t("sharing.settings.addGrant")}
          </Button>
        </div>

        <div className={styles.drawerSection}>
          <Text strong>{t("sharing.settings.reasonLabel")}</Text>
          <Input.TextArea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={t("sharing.settings.reasonPlaceholder")}
            maxLength={200}
            showCount
            autoSize={{ minRows: 2, maxRows: 4 }}
          />
        </div>

        {result && (
          <Alert
            className={styles.resultAlert}
            type={result.status === "pending_approval" ? "warning" : "success"}
            showIcon
            message={
              result.status === "pending_approval"
                ? t("sharing.settings.result.pending")
                : result.applied
                ? t("sharing.settings.result.applied")
                : t("sharing.settings.result.superseded")
            }
            description={
              <div className={styles.resultMeta}>
                <ImpactScopeTag scope={result.impact_scope} />
                <Text type="secondary" className={styles.metaText}>
                  {t("sharing.settings.changeId", { id: result.change_id })}
                </Text>
                {result.status === "pending_approval" && (
                  <Text type="secondary" className={styles.metaText}>
                    {t("sharing.settings.pendingHint")}
                  </Text>
                )}
              </div>
            }
          />
        )}
      </Spin>

      <div className={styles.drawerFooter}>
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button
          type="primary"
          loading={saving}
          disabled={loading}
          style={{
            background: BRAND.color.accent,
            borderColor: BRAND.color.accent,
          }}
          onClick={() => void submit()}
        >
          {t("sharing.settings.submit")}
        </Button>
      </div>
    </Drawer>
  );
}
