/**
 * Admin → Org Units page — /admin/org-units
 *
 * The directory behind every account's resource scope. Reads are part of the
 * users module (the backend mounts ``/api/org-units`` behind ``"users"``) and
 * every write is admin-only, so a non-administrator gets the tree without the
 * edit controls.
 *
 * The two preconditions the server enforces on a delete are mirrored here
 * instead of being left to the refusal: a unit that still has children, or that
 * accounts are still scoped to, gets a disabled delete button carrying the
 * reason. Members come from the account list — the directory payload has no
 * counts. A refusal that slips through anyway (either condition can appear
 * between two loads) is shown with the ``details`` the server sent.
 */

import { useCallback, useEffect, useMemo, useState, type Key } from "react";
import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Tag,
  Tooltip,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { message } from "@/utils/antdMessage";
import { Info, Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { brandName } from "../../../brand.generated";
import PageShell from "../../../layouts/PageShell";
import { ResizableTable } from "../../../components/ResizableTable";
import { EmptyState } from "../../../components/EmptyState";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { isSystemAdmin } from "../../../utils/permissions";
import { normalizeUiLocale } from "../../../utils/localePrefs";
import { pickLocale } from "../../../utils/localizedText";
import { apiErrorMessage, parseApiError } from "../../../utils/apiError";
import {
  PermissionCheckboxPicker,
  fetchPermissionCatalog,
  type PermissionCatalogItem,
} from "../../../components/PermissionPicker";
import {
  orgUnitsApi,
  type OrgUnit,
  type OrgUnitCreateBody,
  type OrgUnitUpdateBody,
} from "../../../api/modules/orgUnits";
import styles from "./index.module.less";

/** A unit plus the children rebuilt from ``parent_key``. */
interface OrgUnitNode extends OrgUnit {
  children: OrgUnitNode[];
}

/** Parent-picker value that means "no parent" — unit keys are never empty. */
const ROOT_VALUE = "";

interface OrgUnitFormValues {
  key: string;
  label_zh: string;
  label_en: string;
  parent_key: string;
  sort_order?: number | null;
}

/** What the server said when it refused a write. */
interface Refusal {
  /** Localized headline — the code's text; the specifics are the other fields. */
  title: string;
  /** Child units that still hang under the refused unit. */
  children: string[];
  /** Accounts still scoped to the refused unit. */
  users: string[];
  /** ``details.reason`` — the one place the precise cause is stated. */
  reason: string;
}

/**
 * Flat list → forest. ``units`` already arrives in display order (the backend
 * orders by ``sort_order, key``), so appending in iteration order keeps siblings
 * ordered without a second sort.
 *
 * A unit is never allowed to become its own ancestor in this view either: a
 * parentage cycle (left by an older write — and exactly what the server's own
 * guard exists to stop) would strand its members outside every root and hide
 * them, so the link that would close the cycle is dropped and that unit takes a
 * root slot instead.
 */
function buildOrgUnitTree(units: OrgUnit[]): OrgUnitNode[] {
  const nodes = new Map<string, OrgUnitNode>(
    units.map((unit) => [unit.key, { ...unit, children: [] }]),
  );
  /** Parents as linked so far — the probe walks the forest being built. */
  const parentOf = new Map<string, string>();
  const closesCycle = (key: string, parentKey: string): boolean => {
    const seen = new Set<string>();
    let current: string | undefined = parentKey;
    while (current !== undefined && !seen.has(current)) {
      if (current === key) return true;
      seen.add(current);
      current = parentOf.get(current);
    }
    return false;
  };

  const roots: OrgUnitNode[] = [];
  for (const unit of units) {
    const node = nodes.get(unit.key);
    if (!node) continue;
    const parentKey = unit.parent_key;
    // A root unit, a parent missing from the list, and a link that would close
    // a cycle all land in the same place: the unit is rendered at the top level.
    const parent =
      parentKey != null && !closesCycle(unit.key, parentKey)
        ? nodes.get(parentKey)
        : undefined;
    if (parent) {
      parent.children.push(node);
      parentOf.set(unit.key, parent.key);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

/** Every key below ``key``, including itself — the unit's own subtree. */
function collectSubtreeKeys(units: OrgUnit[], key: string): Set<string> {
  const childrenOf = new Map<string, string[]>();
  for (const unit of units) {
    if (unit.parent_key == null) continue;
    const siblings = childrenOf.get(unit.parent_key);
    if (siblings) siblings.push(unit.key);
    else childrenOf.set(unit.parent_key, [unit.key]);
  }
  const subtree = new Set<string>([key]);
  const pending = [key];
  while (pending.length > 0) {
    for (const child of childrenOf.get(pending.pop()!) ?? []) {
      if (subtree.has(child)) continue; // re-visit guard: cycles terminate
      subtree.add(child);
      pending.push(child);
    }
  }
  return subtree;
}

/** Detail arrays travel as JSON; anything that is not a string list is ignored. */
function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

/**
 * Split a refused write into a headline plus the specifics the backend sent.
 *
 * The headline comes from the error code's localized text; the specifics come
 * from ``details`` (``children`` / ``users`` / ``reason``). The backend now has
 * dedicated codes for both refusals (`ORG_UNIT_HAS_CHILDREN` / `ORG_UNIT_IN_USE`),
 * so the headline names the right kind of object — but ``details`` is still what
 * tells the administrator exactly which children or members are in the way.
 */
function describeRefusal(
  error: unknown,
  fallback: string,
  t: TFunction,
): Refusal {
  const parsed = parseApiError(error);
  const details = parsed?.details ?? {};
  const codeKey = parsed?.code ? `apiErrors.${parsed.code}` : "";
  const coded = codeKey ? t(codeKey) : "";
  return {
    title: coded && coded !== codeKey ? coded : fallback,
    children: stringList(details.children),
    users: stringList(details.users),
    reason: typeof details.reason === "string" ? details.reason.trim() : "",
  };
}

export default function AdminOrgUnitsPage() {
  const { t, i18n } = useTranslation();
  const currentUser = useCurrentUser();
  /**
   * Who may edit the tree. The backend bounds the *branch* an actor may write
   * (design §2.1: 企业管理员 本企业, 部门管理员 本部门及子部门) and the API only
   * lists the units in that branch, so the page shows the controls to an actor
   * that has any reach at all — a system administrator, an enterprise
   * administrator or a department administrator. A plain employee with the
   * ``users`` key administers no department and is still refused server-side.
   */
  const canManage = useMemo(() => {
    const role = currentUser?.role;
    return (
      isSystemAdmin(currentUser) ||
      role === "enterprise_admin" ||
      role === "unit_admin"
    );
  }, [currentUser]);
  const lang = normalizeUiLocale(i18n.language);
  const [form] = Form.useForm<OrgUnitFormValues>();

  const [units, setUnits] = useState<OrgUnit[]>([]);
  const [membersByUnit, setMembersByUnit] = useState<Map<string, string[]>>(
    new Map(),
  );
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [modalMode, setModalMode] = useState<"create" | "edit">("create");
  const [editTarget, setEditTarget] = useState<OrgUnit | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  /** Collapsed parents; anything absent renders expanded (the tree default). */
  const [collapsedKeys, setCollapsedKeys] = useState<ReadonlySet<string>>(
    new Set(),
  );

  /**
   * Units and their members load together: the delete preconditions need both,
   * so a half-loaded page could offer a delete the server is bound to refuse.
   * Both endpoints sit behind the same ``users`` gate.
   */
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [directory, members] = await Promise.all([
        orgUnitsApi.list(),
        orgUnitsApi.listMembersByUnit(),
      ]);
      setUnits(directory.units ?? []);
      setMembersByUnit(members);
      setLoadError("");
    } catch (err) {
      setLoadError(apiErrorMessage(err, t("orgUnits.loadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const tree = useMemo(() => buildOrgUnitTree(units), [units]);
  /** Keys that have children — the units whose delete the tree blocks. */
  const parentKeys = useMemo(
    () =>
      new Set(
        units.flatMap((unit) => (unit.parent_key ? [unit.parent_key] : [])),
      ),
    [units],
  );
  const expandableKeys = useMemo(
    () => [...parentKeys].filter((key) => !collapsedKeys.has(key)),
    [parentKeys, collapsedKeys],
  );

  const handleExpandedRowsChange = useCallback(
    (keys: readonly Key[]) => {
      const expanded = new Set(keys.map(String));
      setCollapsedKeys(
        new Set([...parentKeys].filter((key) => !expanded.has(key))),
      );
    },
    [parentKeys],
  );

  const parentOptions = useMemo(() => {
    const excluded =
      modalMode === "edit" && editTarget
        ? collectSubtreeKeys(units, editTarget.key)
        : undefined;
    const options = [{ value: ROOT_VALUE, label: t("orgUnits.parentRoot") }];
    const walk = (nodes: OrgUnitNode[], parentLabel: string | null) => {
      for (const node of nodes) {
        // Skipping the subtree, not just the unit: either choice would make the
        // unit its own ancestor, so neither is ever offered.
        if (excluded?.has(node.key)) continue;
        const label = pickLocale(node.label, lang) || node.key;
        // Same "Parent / Child" label convention as the users page's picker.
        options.push({
          value: node.key,
          label: parentLabel ? `${parentLabel} / ${label}` : label,
        });
        walk(node.children, label);
      }
    };
    walk(tree, null);
    return options;
  }, [tree, units, modalMode, editTarget, t, lang]);

  const openCreate = useCallback(() => {
    setModalMode("create");
    setEditTarget(null);
    setRefusal(null);
    setModalOpen(true);
  }, []);

  const openEdit = useCallback((row: OrgUnit) => {
    setModalMode("edit");
    setEditTarget(row);
    setRefusal(null);
    setModalOpen(true);
  }, []);

  // Seed the form on every open. ``sort_order`` is deliberately blank: the list
  // payload carries no order, so prefilling its default would silently reorder
  // the unit on the next save.
  useEffect(() => {
    if (!modalOpen) return;
    if (modalMode === "create") {
      form.resetFields();
      form.setFieldsValue({ parent_key: ROOT_VALUE });
      return;
    }
    if (!editTarget) return;
    form.setFieldsValue({
      key: editTarget.key,
      label_zh: editTarget.label.zh,
      label_en: editTarget.label.en,
      parent_key: editTarget.parent_key ?? ROOT_VALUE,
      sort_order: undefined,
    });
  }, [modalOpen, modalMode, editTarget, form]);

  const handleSubmit = async () => {
    let values: OrgUnitFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return; // antd marks the offending fields on the form itself
    }
    const parentKey =
      values.parent_key === ROOT_VALUE ? null : values.parent_key;
    setSubmitting(true);
    setRefusal(null);
    try {
      if (modalMode === "create") {
        const body: OrgUnitCreateBody = {
          key: values.key.trim(),
          label_zh: values.label_zh.trim(),
          label_en: values.label_en.trim(),
          parent_key: parentKey,
        };
        if (typeof values.sort_order === "number") {
          body.sort_order = values.sort_order;
        }
        await orgUnitsApi.create(body);
        message.success(t("orgUnits.createSuccess"));
      } else if (editTarget) {
        const body: OrgUnitUpdateBody = {
          label_zh: values.label_zh.trim(),
          label_en: values.label_en.trim(),
          // Always sent: the form shows the stored parent, so re-sending it
          // keeps it, and the root sentinel is the explicit ``null`` that lifts
          // a unit back to the top level.
          parent_key: parentKey,
        };
        if (typeof values.sort_order === "number") {
          body.sort_order = values.sort_order;
        }
        await orgUnitsApi.update(editTarget.key, body);
        message.success(t("orgUnits.updateSuccess"));
      }
      setModalOpen(false);
      await load();
    } catch (err) {
      setRefusal(describeRefusal(err, t("orgUnits.actionFailed"), t));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (row: OrgUnit) => {
    setDeletingKey(row.key);
    setRefusal(null);
    try {
      await orgUnitsApi.remove(row.key);
      message.success(t("orgUnits.deleteSuccess"));
    } catch (err) {
      setRefusal(describeRefusal(err, t("orgUnits.actionFailed"), t));
    } finally {
      // Both paths resync: a refusal means this view is stale (a child or a
      // member appeared after the last load), a success means a row is gone.
      setDeletingKey(null);
      await load();
    }
  };

  /**
   * ── module grants (design §2.1/§2.3) ──
   *
   * A department carries keys of its own, and every member of its subtree gains
   * them; ``PUT /api/org-units/{key}/permissions`` is that write. This page is
   * its only caller: without it the API exists and the ability does not.
   */
  const [grantsTarget, setGrantsTarget] = useState<OrgUnit | null>(null);
  const [permCatalog, setPermCatalog] = useState<PermissionCatalogItem[]>([]);
  const [grantKeys, setGrantKeys] = useState<string[]>([]);
  const [grantsLoading, setGrantsLoading] = useState(false);
  const [grantsSaving, setGrantsSaving] = useState(false);
  const [grantsError, setGrantsError] = useState("");

  /**
   * The picker's catalog: every key this operator may hand out, plus the keys
   * the department already holds.
   *
   * The second half is what keeps a save from dropping a grant the operator
   * does not hold itself: the server resolves ``assert_can_grant`` against the
   * department's own set, so a stored key is accepted — and an untick really
   * does revoke it. A key no catalog entry describes (a row written by an older
   * build) is shown too, or the replace would delete what was never displayed.
   */
  const grantCatalog = useMemo(() => {
    const stored = new Set(grantKeys);
    const known = new Set(permCatalog.map((p) => p.key));
    const items = permCatalog.filter((p) => p.can_grant || stored.has(p.key));
    for (const key of stored) {
      if (!known.has(key)) {
        items.push({ key, category: "admin", label: key, can_grant: false });
      }
    }
    return items;
  }, [permCatalog, grantKeys]);

  const openGrants = useCallback(
    (row: OrgUnit) => {
      setGrantsTarget(row);
      setGrantKeys([]);
      setGrantsError("");
      setGrantsLoading(true);
      Promise.all([
        orgUnitsApi.getPermissions(row.key),
        permCatalog.length > 0
          ? Promise.resolve(permCatalog)
          : fetchPermissionCatalog(),
      ])
        .then(([stored, catalog]) => {
          setPermCatalog(catalog);
          setGrantKeys(stored.permissions ?? []);
        })
        .catch((err) => {
          // The form stays closed on purpose: an empty picker after a failed
          // load would read as "this department has no access".
          setGrantsError(apiErrorMessage(err, t("perms.grantsLoadFailed"), t));
        })
        .finally(() => setGrantsLoading(false));
    },
    [permCatalog, t],
  );

  const saveGrants = async () => {
    if (!grantsTarget) return;
    setGrantsSaving(true);
    try {
      await orgUnitsApi.setPermissions(grantsTarget.key, grantKeys);
      message.success(t("perms.grantsSaved"));
      setGrantsTarget(null);
    } catch (err) {
      setGrantsError(apiErrorMessage(err, t("orgUnits.actionFailed"), t));
    } finally {
      setGrantsSaving(false);
    }
  };

  const columns = useMemo<ColumnsType<OrgUnitNode>>(() => {
    const cols: ColumnsType<OrgUnitNode> = [
      {
        key: "name",
        title: t("orgUnits.colName"),
        width: 260,
        render: (_, row) => (
          <div className={styles.unitCell}>
            <span className={styles.unitName}>
              {pickLocale(row.label, lang) || row.key}
            </span>
            <span className={styles.unitKey}>{row.key}</span>
          </div>
        ),
      },
      {
        key: "names",
        title: t("orgUnits.colNames"),
        width: 220,
        render: (_, row) => (
          <span className={styles.unitNames}>
            {row.label.zh} · {row.label.en}
          </span>
        ),
      },
      {
        key: "members",
        title: t("orgUnits.colMembers"),
        width: 220,
        render: (_, row) => {
          const members = membersByUnit.get(row.key) ?? [];
          if (members.length === 0) return t("orgUnits.noMembers");
          return (
            <span className={styles.memberTags}>
              {members.map((name) => (
                <Tag key={name}>{name}</Tag>
              ))}
            </span>
          );
        },
      },
    ];
    if (!canManage) return cols;
    cols.push({
      key: "actions",
      title: t("common.actions"),
      width: 150,
      align: "right",
      render: (_, row) => {
        // The server's delete preconditions, mirrored: a unit with children and
        // a unit with members are both refused, so the action is disabled here
        // with that reason instead of failing after the click.
        let blocker = "";
        if (parentKeys.has(row.key)) {
          blocker = t("orgUnits.deleteBlockedChildren");
        } else if ((membersByUnit.get(row.key) ?? []).length > 0) {
          blocker = t("orgUnits.deleteBlockedMembers");
        } else if (deletingKey === row.key) {
          blocker = t("orgUnits.deletePending");
        }
        return (
          <div className={styles.tableActions}>
            <Tooltip title={t("perms.grantsAction")}>
              <Button
                type="text"
                size="small"
                icon={<ShieldCheck size={15} />}
                aria-label={t("perms.grantsAction")}
                onClick={() => openGrants(row)}
              />
            </Tooltip>
            <Button
              type="text"
              size="small"
              icon={<Pencil size={15} />}
              aria-label={t("common.edit")}
              onClick={() => openEdit(row)}
            />
            {blocker ? (
              // A native disabled control emits no pointer events, so the
              // tooltip needs an anchor element of its own.
              <Tooltip title={blocker}>
                <span className={styles.disabledAction}>
                  <Button
                    type="text"
                    danger
                    size="small"
                    disabled
                    icon={<Trash2 size={15} />}
                    aria-label={t("common.delete")}
                  />
                </span>
              </Tooltip>
            ) : (
              <Popconfirm
                title={t("orgUnits.deleteConfirmTitle", {
                  name: pickLocale(row.label, lang) || row.key,
                })}
                description={t("orgUnits.deleteConfirmHint")}
                okText={t("common.delete")}
                cancelText={t("common.cancel")}
                okButtonProps={{ danger: true }}
                onConfirm={() => void handleDelete(row)}
              >
                <Button
                  type="text"
                  danger
                  size="small"
                  icon={<Trash2 size={15} />}
                  aria-label={t("common.delete")}
                />
              </Popconfirm>
            )}
          </div>
        );
      },
    });
    return cols;
  }, [
    t,
    lang,
    canManage,
    membersByUnit,
    parentKeys,
    deletingKey,
    openEdit,
    openGrants,
  ]);

  // One element, placed in the pane below — or inside the modal while it is
  // open, so a refused create/update is never reported behind the mask.
  const refusalAlert = refusal ? (
    <Alert
      type="error"
      showIcon
      closable
      message={refusal.title}
      onClose={() => setRefusal(null)}
      description={
        <>
          {refusal.children.length > 0 ? (
            <div>
              {t("orgUnits.blockedChildren")}
              {refusal.children.map((key) => (
                <Tag key={key}>{key}</Tag>
              ))}
            </div>
          ) : null}
          {refusal.users.length > 0 ? (
            <div>
              {t("orgUnits.blockedMembers")}
              {refusal.users.map((name) => (
                <Tag key={name}>{name}</Tag>
              ))}
            </div>
          ) : null}
          {refusal.reason ? (
            <div className={styles.errorReason}>
              {t("orgUnits.reportReason")}
              {refusal.reason}
            </div>
          ) : null}
        </>
      }
    />
  ) : null;

  return (
    <PageShell
      title={t("pageShell.adminOrgUnits.title")}
      subtitle={t("pageShell.adminOrgUnits.subtitle")}
      actions={
        canManage ? (
          <Button type="primary" icon={<Plus size={15} />} onClick={openCreate}>
            {t("orgUnits.newUnit")}
          </Button>
        ) : (
          <span className={styles.adminHint}>
            <Info size={14} />
            {t("orgUnits.adminOnlyHint", {
              brand: brandName(i18n.language),
            })}
          </span>
        )
      }
    >
      <div className={styles.panel}>
        {modalOpen ? null : refusalAlert}

        {loadError ? (
          <EmptyState
            variant="error"
            title={t("orgUnits.loadFailed")}
            description={loadError}
            actionLabel={t("common.refresh")}
            onAction={() => void load()}
          />
        ) : units.length === 0 && !loading ? (
          <EmptyState
            className={styles.empty}
            title={t("orgUnits.emptyTitle")}
            description={t("orgUnits.emptyHint")}
            actionLabel={canManage ? t("orgUnits.newUnit") : undefined}
            onAction={canManage ? openCreate : undefined}
          />
        ) : (
          <>
            <div className={styles.toolbar}>
              <div className={styles.toolbarInfo}>
                <span className={styles.toolbarCount}>
                  {t("orgUnits.unitCount", { total: units.length })}
                </span>
                {/* Both delete preconditions, stated before any click. */}
                {canManage ? (
                  <span className={styles.rulesHint}>
                    {t("orgUnits.deleteRules")}
                  </span>
                ) : null}
              </div>
            </div>
            <ResizableTable
              storageKey="admin-org-units"
              rowKey="key"
              size="middle"
              loading={loading}
              columns={columns}
              dataSource={tree}
              pagination={false}
              scroll={{ x: "max-content" }}
              expandable={{
                expandedRowKeys: expandableKeys,
                onExpandedRowsChange: handleExpandedRowsChange,
                indentSize: 20,
              }}
            />
          </>
        )}

        <Modal
          open={modalOpen}
          title={
            modalMode === "create"
              ? t("orgUnits.newUnit")
              : t("orgUnits.editUnit")
          }
          okText={
            modalMode === "create" ? t("common.create") : t("common.save")
          }
          cancelText={t("common.cancel")}
          confirmLoading={submitting}
          onOk={() => void handleSubmit()}
          onCancel={() => setModalOpen(false)}
          afterClose={() => setSubmitting(false)}
        >
          {refusalAlert}
          <Form form={form} layout="vertical">
            <Form.Item
              name="key"
              label={t("orgUnits.fieldKey")}
              rules={[
                {
                  required: true,
                  whitespace: true,
                  message: t("orgUnits.fieldRequired", {
                    label: t("orgUnits.fieldKey"),
                  }),
                },
                { max: 64, message: t("orgUnits.keyTooLong") },
              ]}
              extra={modalMode === "create" ? t("orgUnits.keyHint") : undefined}
            >
              <Input
                disabled={modalMode === "edit"}
                placeholder={t("orgUnits.keyPlaceholder")}
              />
            </Form.Item>
            <Form.Item
              name="label_zh"
              label={t("orgUnits.fieldLabelZh")}
              rules={[
                {
                  required: true,
                  whitespace: true,
                  message: t("orgUnits.fieldRequired", {
                    label: t("orgUnits.fieldLabelZh"),
                  }),
                },
                { max: 100, message: t("orgUnits.labelTooLong") },
              ]}
            >
              <Input />
            </Form.Item>
            <Form.Item
              name="label_en"
              label={t("orgUnits.fieldLabelEn")}
              rules={[
                {
                  required: true,
                  whitespace: true,
                  message: t("orgUnits.fieldRequired", {
                    label: t("orgUnits.fieldLabelEn"),
                  }),
                },
                { max: 100, message: t("orgUnits.labelTooLong") },
              ]}
            >
              <Input />
            </Form.Item>
            <Form.Item name="parent_key" label={t("orgUnits.fieldParent")}>
              <Select options={parentOptions} />
            </Form.Item>
            <Form.Item
              name="sort_order"
              label={t("orgUnits.fieldSortOrder")}
              extra={t("orgUnits.sortOrderHint")}
            >
              <InputNumber
                style={{ width: "100%" }}
                placeholder={t("common.unchanged")}
              />
            </Form.Item>
          </Form>
        </Modal>

        {canManage ? (
          <Drawer
            title={
              grantsTarget
                ? t("perms.grantsTitle", {
                    unit:
                      pickLocale(grantsTarget.label, lang) || grantsTarget.key,
                  })
                : ""
            }
            placement="right"
            open={grantsTarget !== null}
            onClose={() => setGrantsTarget(null)}
            width={Math.min(
              520,
              typeof window !== "undefined" ? window.innerWidth - 24 : 520,
            )}
            footer={
              <div className={styles.grantsFooter}>
                <Button onClick={() => setGrantsTarget(null)}>
                  {t("common.cancel")}
                </Button>
                <Button
                  type="primary"
                  loading={grantsSaving}
                  disabled={grantsLoading || grantsError !== ""}
                  onClick={() => void saveGrants()}
                >
                  {t("perms.grantsSave")}
                </Button>
              </div>
            }
          >
            <p className={styles.grantsHint}>{t("perms.grantsHint")}</p>
            <p className={styles.grantsHint}>
              {t("perms.grantsInheritedNote")}
            </p>
            {grantsError ? (
              <Alert type="error" showIcon message={grantsError} />
            ) : null}
            {grantsLoading ? (
              <Spin />
            ) : grantsError ? null : grantCatalog.length === 0 ? (
              <p className={styles.grantsEmpty}>{t("perms.grantsEmpty")}</p>
            ) : (
              <PermissionCheckboxPicker
                catalog={grantCatalog}
                value={grantKeys}
                onChange={setGrantKeys}
              />
            )}
          </Drawer>
        ) : null}
      </div>
    </PageShell>
  );
}
