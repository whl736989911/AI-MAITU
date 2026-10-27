import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Divider,
  Drawer,
  Input,
  Radio,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
} from "antd";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  knowledgePermissionsApi,
  type KnowledgePermissionMetadata,
  type KnowledgePermissionPrincipals,
} from "../../../api/modules/knowledgePermissions";
import type {
  SharingGrant,
  SharingGranteeType,
  SharingPermission,
  SharingVisibility,
} from "../../../api/modules/sharing";
import { apiErrorMessage } from "../../../utils/apiError";
import styles from "../index.module.less";

type Props = {
  baseId: string;
  open: boolean;
  metadata: KnowledgePermissionMetadata | null;
  isDirectory: boolean;
  onClose: () => void;
  onSaved: (metadata: KnowledgePermissionMetadata) => void;
};

const GRANTEE_TYPES: SharingGranteeType[] = ["user", "unit", "role"];

export default function KnowledgePermissionsDrawer({
  baseId,
  open,
  metadata,
  isDirectory,
  onClose,
  onSaved,
}: Props) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [visibility, setVisibility] = useState<SharingVisibility | "custom">(
    "private",
  );
  const [permission, setPermission] = useState<SharingPermission>("read");
  const [unitKey, setUnitKey] = useState<string | null>(null);
  const [grants, setGrants] = useState<SharingGrant[]>([]);
  const [grantType, setGrantType] = useState<SharingGranteeType>("user");
  const [grantId, setGrantId] = useState("");
  const [reason, setReason] = useState("");
  const [principals, setPrincipals] =
    useState<KnowledgePermissionPrincipals | null>(null);
  const [expectedVersion, setExpectedVersion] = useState(0);
  const [delegations, setDelegations] = useState<{
    principals: { id: number; username: string; label: string }[];
    grants: {
      id: string;
      grantor_user_id: number;
      grantee_user_id: number;
      grantee_label: string;
      can_delegate: boolean;
      can_revoke: boolean;
    }[];
  } | null>(null);
  const [delegationRecipient, setDelegationRecipient] = useState<number>();
  const [delegationCanDelegate, setDelegationCanDelegate] = useState(false);
  const [delegationLoading, setDelegationLoading] = useState(false);
  const [delegationSaving, setDelegationSaving] = useState(false);

  useEffect(() => {
    if (!open || !metadata) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setReason("");
    const fetchMetadata =
      metadata.resource_type === "knowledge_base"
        ? knowledgePermissionsApi
            .list(baseId)
            .then((result) => result.knowledge_base)
        : knowledgePermissionsApi.getDocument(baseId, metadata.resource_id);
    fetchMetadata
      .then((current) => {
        if (cancelled) return;
        const entry = current.entry;
        // Display the effective scope above, but edit the explicit ACL itself.
        setVisibility(
          entry
            ? entry.visibility === "public"
              ? "public"
              : entry.grants.length > 0
              ? "custom"
              : entry.visibility
            : current.access_scope === "company"
            ? "public"
            : current.access_scope === "department"
            ? "unit"
            : current.access_scope === "custom"
            ? "custom"
            : "private",
        );
        setExpectedVersion(current.version);
        setPermission(current.entry?.permission ?? "read");
        setUnitKey(current.entry?.unit_key ?? null);
        const configuredGrants = entry?.grants ?? [];
        const departmentKey =
          entry?.visibility === "unit" && configuredGrants.length > 0
            ? entry.unit_key
            : null;
        setGrants(
          departmentKey &&
            !configuredGrants.some(
              (grant) =>
                grant.grantee_type === "unit" &&
                grant.grantee_id === departmentKey,
            )
            ? [
                ...configuredGrants,
                { grantee_type: "unit", grantee_id: departmentKey },
              ]
            : configuredGrants,
        );
        setError(null);
      })
      .catch((cause) => {
        if (!cancelled) setError(cause);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [baseId, metadata, open]);

  useEffect(() => {
    if (!open || !metadata?.can_manage) {
      setPrincipals(null);
      return;
    }
    let cancelled = false;
    knowledgePermissionsApi
      .principals(baseId)
      .then((result) => {
        if (!cancelled) setPrincipals(result);
      })
      .catch((cause) => {
        if (!cancelled) setError(cause);
      });
    return () => {
      cancelled = true;
    };
  }, [baseId, metadata?.can_manage, open]);
  useEffect(() => {
    if (
      !open ||
      !metadata ||
      metadata.resource_type !== "knowledge_document" ||
      isDirectory ||
      (!metadata.can_manage && !metadata.can_delegate)
    ) {
      setDelegations(null);
      return;
    }
    let cancelled = false;
    setDelegationLoading(true);
    knowledgePermissionsApi
      .delegations(baseId, metadata.resource_id)
      .then((result) => {
        if (!cancelled) {
          setDelegations(result);
          setDelegationRecipient(undefined);
        }
      })
      .catch((cause) => {
        if (!cancelled) setError(cause);
      })
      .finally(() => {
        if (!cancelled) setDelegationLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [baseId, isDirectory, metadata, open]);

  const unitOptions = (principals?.departments ?? []).map((unit) => ({
    value: unit.key,
    label: unit.label,
  }));
  const principalOptions = useMemo(() => {
    if (grantType === "user") {
      return (principals?.users ?? []).map((user) => ({
        value: String(user.id),
        label: user.label,
      }));
    }
    if (grantType === "unit") {
      return (principals?.departments ?? []).map((unit) => ({
        value: unit.key,
        label: unit.label,
      }));
    }
    return (principals?.roles ?? []).map((role) => ({
      value: role.id,
      label: role.label,
    }));
  }, [grantType, principals]);

  const scopeInvalid =
    (visibility === "unit" && !unitKey) ||
    (visibility === "custom" && grants.length === 0);

  const grantLabel = (grant: SharingGrant) => {
    if (grant.grantee_type === "user") {
      return (
        principals?.users.find((user) => String(user.id) === grant.grantee_id)
          ?.label ?? grant.grantee_id
      );
    }
    if (grant.grantee_type === "unit") {
      return (
        principals?.departments.find((unit) => unit.key === grant.grantee_id)
          ?.label ?? grant.grantee_id
      );
    }
    return (
      principals?.roles.find((role) => role.id === grant.grantee_id)?.label ??
      grant.grantee_id
    );
  };

  const save = async () => {
    if (!metadata || scopeInvalid || loading) return;
    setSaving(true);
    setError(null);
    try {
      const body = {
        expected_version: expectedVersion,
        visibility: visibility === "custom" ? "private" : visibility,
        unit_key: visibility === "unit" ? unitKey : null,
        permission,
        grants: (visibility === "custom" ? grants : [])
          .filter((grant) => grant.grantee_id.trim())
          .map((grant) => ({
            ...grant,
            grantee_id: grant.grantee_id.trim(),
          })),
        reason: reason.trim() || null,
      };
      const response =
        metadata.resource_type === "knowledge_base"
          ? await knowledgePermissionsApi.updateBase(baseId, body)
          : await knowledgePermissionsApi.updateDocument(
              baseId,
              metadata.resource_id,
              body,
            );
      onSaved(response.permission);
      setError(null);
    } catch (cause) {
      setError(cause);
    } finally {
      setSaving(false);
    }
  };

  const addGrant = () => {
    const value = grantId.trim();
    if (
      !value ||
      grants.some(
        (grant) =>
          grant.grantee_type === grantType && grant.grantee_id === value,
      )
    )
      return;
    setGrants((current) => [
      ...current,
      {
        grantee_type: grantType,
        grantee_id: value,
        ...(grantType === "user" ? { can_delegate: false } : {}),
      },
    ]);
    setGrantId("");
  };
  const issueDelegation = async () => {
    if (
      !metadata ||
      metadata.resource_type !== "knowledge_document" ||
      isDirectory ||
      !delegationRecipient ||
      !delegations?.principals.some(
        (principal) => principal.id === delegationRecipient,
      )
    )
      return;
    setDelegationSaving(true);
    try {
      const result = await knowledgePermissionsApi.addDelegation(
        baseId,
        metadata.resource_id,
        {
          grantee_user_id: delegationRecipient,
          can_delegate: delegationCanDelegate,
        },
      );
      setDelegations(result);
      setDelegationRecipient(undefined);
      setDelegationCanDelegate(false);
    } catch (cause) {
      setError(cause);
    } finally {
      setDelegationSaving(false);
    }
  };

  const revokeDelegation = async (delegationId: string) => {
    if (
      !metadata ||
      isDirectory ||
      metadata.resource_type !== "knowledge_document"
    )
      return;
    setDelegationSaving(true);
    try {
      await knowledgePermissionsApi.revokeDelegation(
        baseId,
        metadata.resource_id,
        delegationId,
      );
      const result = await knowledgePermissionsApi.delegations(
        baseId,
        metadata.resource_id,
      );
      setDelegations(result);
    } catch (cause) {
      setError(cause);
    } finally {
      setDelegationSaving(false);
    }
  };

  return (
    <Drawer
      title={t("knowledgeBases.permissions.title")}
      open={open}
      onClose={onClose}
      width={480}
      destroyOnHidden
      extra={
        metadata?.can_manage ? (
          <Button
            type="primary"
            loading={saving}
            disabled={loading || scopeInvalid}
            onClick={() => void save()}
          >
            {t("common.save")}
          </Button>
        ) : null
      }
    >
      {!metadata ? null : (
        <Spin spinning={loading}>
          <Typography.Paragraph strong>
            {metadata.path || "/"}
          </Typography.Paragraph>
          <Space wrap>
            <Tag
              color={
                metadata.access_scope === "company"
                  ? "green"
                  : metadata.access_scope === "department"
                  ? "blue"
                  : metadata.access_scope === "personal"
                  ? "default"
                  : "purple"
              }
            >
              {metadata.access_scope === "company"
                ? t("knowledgeBases.permissions.company")
                : metadata.access_scope === "department"
                ? metadata.department_name ||
                  t("knowledgeBases.permissions.department")
                : metadata.access_scope === "personal"
                ? t("knowledgeBases.permissions.personal")
                : t("knowledgeBases.permissions.custom")}
            </Tag>
            <Tag>
              {t("knowledgeBases.permissions.read")}:{" "}
              {t(
                metadata.effective.read
                  ? "knowledgeBases.permissions.allowed"
                  : "knowledgeBases.permissions.denied",
              )}
            </Tag>
            <Tag>
              {t("knowledgeBases.permissions.write")}:{" "}
              {t(
                metadata.effective.write
                  ? "knowledgeBases.permissions.allowed"
                  : "knowledgeBases.permissions.denied",
              )}
            </Tag>
          </Space>
          {metadata.inherited_from ? (
            <Alert
              className={styles.permissionAlert}
              type="info"
              showIcon
              message={t("knowledgeBases.permissions.inherited", {
                path: metadata.inherited_from.path,
              })}
            />
          ) : null}
          {metadata.pending ? (
            <Alert
              className={styles.permissionAlert}
              type="warning"
              showIcon
              message={t("knowledgeBases.permissions.pending", {
                status: metadata.pending.status,
              })}
            />
          ) : null}
          {error ? (
            <Alert
              className={styles.permissionAlert}
              type="error"
              showIcon
              message={apiErrorMessage(
                error,
                t("knowledgeBases.permissions.failed"),
                t,
              )}
            />
          ) : null}
          {!metadata.can_manage ? null : (
            <>
              <Divider />
              <Typography.Text strong>
                {t("knowledgeBases.permissions.configure")}
              </Typography.Text>
              <Radio.Group
                className={styles.permissionChoices}
                value={visibility}
                onChange={(event) => setVisibility(event.target.value)}
                options={(
                  [
                    ["private", t("knowledgeBases.permissions.personal")],
                    ["unit", t("knowledgeBases.permissions.department")],
                    ["public", t("knowledgeBases.permissions.company")],
                    ["custom", t("knowledgeBases.permissions.custom")],
                  ] as const
                ).map(([value, label]) => ({ value, label }))}
              />
              {visibility === "unit" ? (
                <Select
                  className={styles.permissionField}
                  value={unitKey ?? undefined}
                  onChange={setUnitKey}
                  options={unitOptions}
                  placeholder={t("knowledgeBases.permissions.selectDepartment")}
                />
              ) : null}
              <Radio.Group
                className={styles.permissionChoices}
                value={permission}
                onChange={(event) => setPermission(event.target.value)}
                options={(
                  [
                    ["read", t("knowledgeBases.permissions.read")],
                    ["write", t("knowledgeBases.permissions.write")],
                  ] as const
                ).map(([value, label]) => ({ value, label }))}
              />
              {visibility === "custom" || visibility === "unit" ? (
                <>
                  <Typography.Text type="secondary">
                    {t("knowledgeBases.permissions.grantsHint")}
                  </Typography.Text>
                  <Space.Compact className={styles.permissionField} block>
                    <Select
                      value={grantType}
                      onChange={(value) => {
                        setGrantType(value);
                        setGrantId("");
                      }}
                      options={GRANTEE_TYPES.map((value) => ({
                        value,
                        label: t(`sharing.settings.granteeType.${value}`),
                      }))}
                    />
                    <Select
                      showSearch
                      optionFilterProp="label"
                      value={grantId || undefined}
                      onChange={setGrantId}
                      options={principalOptions}
                      placeholder={t(
                        "knowledgeBases.permissions.selectGrantee",
                      )}
                    />
                    <Button
                      icon={<Plus size={14} />}
                      onClick={addGrant}
                      disabled={!grantId}
                      aria-label={t("knowledgeBases.permissions.addGrant")}
                    />
                  </Space.Compact>
                  <Space
                    direction="vertical"
                    className={styles.permissionField}
                  >
                    {grants.map((grant, index) => (
                      <Space
                        key={`${grant.grantee_type}:${grant.grantee_id}:${index}`}
                      >
                        <Tag>
                          {t(
                            `sharing.settings.granteeType.${grant.grantee_type}`,
                          )}{" "}
                          · {grantLabel(grant)}
                        </Tag>
                        {metadata.can_manage &&
                        grant.grantee_type === "user" ? (
                          <Space size={4}>
                            <Typography.Text type="secondary">
                              {t("knowledgeBases.permissions.allowDelegation")}
                            </Typography.Text>
                            <Switch
                              size="small"
                              checked={grant.can_delegate === true}
                              onChange={(checked) =>
                                setGrants((current) =>
                                  current.map((item, itemIndex) =>
                                    itemIndex === index
                                      ? { ...item, can_delegate: checked }
                                      : item,
                                  ),
                                )
                              }
                              aria-label={t(
                                "knowledgeBases.permissions.allowDelegation",
                              )}
                            />
                          </Space>
                        ) : null}
                        <Button
                          type="text"
                          danger
                          icon={<Trash2 size={14} />}
                          aria-label={t(
                            "knowledgeBases.permissions.removeGrant",
                            { id: grant.grantee_id },
                          )}
                          onClick={() =>
                            setGrants((current) =>
                              current.filter(
                                (_, itemIndex) => itemIndex !== index,
                              ),
                            )
                          }
                        />
                      </Space>
                    ))}
                  </Space>
                </>
              ) : null}
              <Input.TextArea
                className={styles.permissionField}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder={t("knowledgeBases.permissions.reason")}
                autoSize={{ minRows: 2, maxRows: 4 }}
              />
              <Button
                type="primary"
                loading={saving}
                disabled={loading || scopeInvalid}
                onClick={() => void save()}
              >
                {t("knowledgeBases.permissions.save")}
              </Button>
            </>
          )}
          {(metadata.resource_type === "knowledge_base" || isDirectory) &&
          metadata.can_delegate ? (
            <Alert
              className={styles.permissionAlert}
              type="info"
              showIcon
              message={t("knowledgeBases.permissions.delegateOnDocument")}
            />
          ) : null}
          {metadata.resource_type === "knowledge_document" &&
          !isDirectory &&
          (metadata.can_manage || metadata.can_delegate) ? (
            <>
              <Divider />
              <Typography.Text strong>
                {t("knowledgeBases.permissions.delegatedSharing")}
              </Typography.Text>
              <Alert
                className={styles.permissionAlert}
                type="warning"
                showIcon
                message={t("knowledgeBases.permissions.cascadeWarning")}
              />
              {delegationLoading ? <Spin size="small" /> : null}
              {(metadata.can_delegate || metadata.can_manage) && delegations ? (
                <>
                  <Select
                    className={styles.permissionField}
                    showSearch
                    optionFilterProp="label"
                    value={delegationRecipient}
                    onChange={setDelegationRecipient}
                    options={delegations.principals.map((principal) => ({
                      value: principal.id,
                      label: principal.label,
                    }))}
                    placeholder={t("knowledgeBases.permissions.selectDelegate")}
                  />
                  <Space className={styles.permissionField}>
                    <Typography.Text>
                      {t("knowledgeBases.permissions.allowFurtherDelegation")}
                    </Typography.Text>
                    <Switch
                      checked={delegationCanDelegate}
                      onChange={setDelegationCanDelegate}
                      aria-label={t(
                        "knowledgeBases.permissions.allowFurtherDelegation",
                      )}
                    />
                  </Space>
                  <Button
                    type="primary"
                    loading={delegationSaving}
                    disabled={
                      !delegationRecipient ||
                      !delegations.principals.some(
                        (principal) => principal.id === delegationRecipient,
                      )
                    }
                    onClick={() => void issueDelegation()}
                  >
                    {t("knowledgeBases.permissions.grantDelegation")}
                  </Button>
                </>
              ) : null}
              <Space direction="vertical" className={styles.permissionField}>
                {delegations?.grants.map((grant) => (
                  <Space key={grant.id} wrap>
                    <Tag>{grant.grantee_label}</Tag>
                    {metadata.can_manage ? (
                      <Typography.Text type="secondary">
                        {t("knowledgeBases.permissions.grantedBy", {
                          id: grant.grantor_user_id,
                        })}
                      </Typography.Text>
                    ) : null}
                    {grant.can_delegate ? (
                      <Tag>{t("knowledgeBases.permissions.canDelegate")}</Tag>
                    ) : null}
                    {grant.can_revoke ? (
                      <Button
                        size="small"
                        danger
                        loading={delegationSaving}
                        onClick={() => void revokeDelegation(grant.id)}
                      >
                        {t("knowledgeBases.permissions.revokeDelegation")}
                      </Button>
                    ) : null}
                  </Space>
                ))}
              </Space>
            </>
          ) : null}
        </Spin>
      )}
    </Drawer>
  );
}
