/**
 * Admin → Users page (plan §14.7).
 *
 * List all users with role/disabled toggles, password reset, delete.
 * Card and table views (default table). The view switcher + refresh +
 * new-user buttons live in a content-area toolbar (mirrors the Experts
 * page layout). Each row/card shows agent count; click opens a drawer
 * with that user's agents.
 *
 * Authorization mirrors ``src/octop/api/routers/users.py`` (997fe63): the
 * ``users`` module key opens the surface, the ``admin`` role owns everything
 * that moves the boundary. A control the actor may not use is hidden, never
 * disabled.
 *   GET    /api/users                        ``users``
 *   POST   /api/users                        ``users``; org_unit needs admin
 *   PATCH  /api/users/{id}                   ``users``; role / org_unit /
 *                                            disabled / denied_permissions
 *                                            need admin (``_assert_admin`` /
 *                                            ``_assert_can_administer``)
 *   POST   /api/users/{id}/reset-password    admin
 *   POST   /api/users/{id}/unlock-login      ``users``
 *   DELETE /api/users/{id}                   admin (``require_admin``)
 *
 * ``denied_permissions`` is the last leg of ``role ∪ unit ∪ grant − deny``
 * and outranks the other three, so the edit drawer never shows a tick without
 * saying whether it survives: keys the department grants are tagged, and a
 * key on the deny list is flagged wherever it appears. On the wire the field
 * is three-state — omitted keeps the stored list, ``null`` clears it, an
 * array replaces it — so an untouched picker sends nothing.
 */

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  Button,
  Modal,
  Form,
  Input,
  Space,
  Popconfirm,
  Switch,
  Typography,
  Tooltip,
  Drawer,
  Empty,
  Spin,
  Tag,
  Segmented,
  Select,
  Checkbox,
  InputNumber,
} from "antd";
import { message } from "@/utils/antdMessage";
import { ResizableTable } from "@/components/ResizableTable";

import {
  Ban,
  Bot,
  Building2,
  Check,
  ChevronRight,
  CircleHelp,
  Clock,
  IdCard,
  KeyRound,
  LayoutGrid,
  Link2,
  List,
  Lock,
  LockOpen,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  ShieldOff,
  Trash2,
  TriangleAlert,
  User,
  UserRound,
  Mail,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { LucideIcon } from "lucide-react";
import { request } from "../../../api/request";
import { authApi } from "../../../api/modules/auth";
import type { OctopRole } from "../../../api/modules/auth";
import { normalizeUiLocale } from "../../../utils/localePrefs";
import { pickLocale } from "../../../utils/localizedText";
import { isSystemAdmin } from "../../../utils/permissions";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useCardTableView } from "../../../hooks/useCardTableView";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import type { OctopAgent } from "../../../context/AgentContext";
import { isFeatureAgent } from "../../../utils/agentKind";
import { AgentCard } from "../../Experts/components/AgentCard";
import EditAgentDrawer from "../../Experts/components/EditAgentDrawer";
import InviteDrawer from "./InviteDrawer";
import RootDirSelect from "../../Experts/components/RootDirSelect";
import { fetchFilesystemDefaults } from "../../Experts/components/agentBackendForm";
import { HOST_FS_ROOT } from "../../Experts/components/rootDirTree";
import expertStyles from "../../Experts/index.module.less";
import styles from "./index.module.less";

const { Text } = Typography;

interface UserRow {
  id: number;
  username: string;
  role: OctopRole;
  display_name: string | null;
  email?: string | null;
  has_password?: boolean;
  sso_linked?: boolean;
  disabled: boolean;
  login_failed_count?: number;
  login_locked?: boolean;
  login_locked_until?: number;
  login_retry_after_seconds?: number;
  created_at?: number;
  permissions?: string[];
  /**
   * Keys this account is refused, whoever grants them (``role ∪ unit ∪ grant
   * − deny``). Admin-writable only; absent from an older backend, in which
   * case the deny editor simply reads as empty.
   */
  denied_permissions?: string[];
  /** Org unit key — non-admin accounts can carry the unit scope. */
  org_unit?: string | null;
  workspace_root_dir?: string | null;
  token_quota?: number | null;
}

/** Org unit catalog row (``GET /api/org-units``). */
interface OrgUnit {
  key: string;
  label: { zh?: string; en?: string };
  parent_key: string | null;
}

/**
 * Org units for the role picker. Admin-only endpoint; a failure just leaves
 * the selector empty, so a stale/absent backend never blocks the page.
 */
function fetchOrgUnits(): Promise<OrgUnit[]> {
  return request<{ units: OrgUnit[] }>("/org-units").then(
    (res) => res.units ?? [],
  );
}

/** Role → i18n label key. Exhaustive so a new role fails the build here. */
const ROLE_LABEL_KEYS: Record<OctopRole, string> = {
  admin: "adminUsers.roleAdmin",
  unit_admin: "adminUsers.roleUnitAdmin",
  user: "adminUsers.roleUser",
};

/** Role → picker icon, same exhaustive mapping. */
const ROLE_ICONS: Record<OctopRole, LucideIcon> = {
  admin: ShieldCheck,
  unit_admin: Building2,
  user: UserRound,
};

interface PermissionCatalogItem {
  key: string;
  category: string;
  label: string;
  page?: string;
  page_label?: string;
}

function permFullLabel(item: PermissionCatalogItem): string {
  if (item.page_label) return `${item.page_label} / ${item.label}`;
  return item.label;
}

interface PolicyFormValues {
  limit_workspace_root?: boolean;
  workspace_root_dir?: string;
  limit_token_quota?: boolean;
  token_quota?: number | null;
}

interface CreateValues extends PolicyFormValues {
  username: string;
  display_name?: string;
  email?: string;
  password: string;
  confirm: string;
  role: OctopRole;
  org_unit?: string;
  permissions?: string[];
}

interface EditValues extends PolicyFormValues {
  display_name?: string;
  email?: string;
  role: OctopRole;
  org_unit?: string;
  permissions?: string[];
  /** Deny list; only sent once the picker was touched (see ``denyTouched``). */
  denied_permissions?: string[];
}

interface ResetValues {
  password: string;
  confirm: string;
}

function roleToneClass(role: OctopRole): string {
  if (role === "admin") return styles.roleToneAdmin;
  if (role === "unit_admin") return styles.roleToneUnitAdmin;
  return styles.roleToneUser;
}

function useNowSeconds(active: boolean): number {
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(
      () => setNow(Math.floor(Date.now() / 1000)),
      1000,
    );
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

function lockRemainingSeconds(row: UserRow, nowSec: number): number {
  if (!row.login_locked || !row.login_locked_until) return 0;
  return Math.max(0, row.login_locked_until - nowSec);
}

function formatUserTs(ts: number | undefined, timeZone: string): string {
  if (!ts) return "—";
  return formatServerDateTime(ts, timeZone);
}

interface UserCardGridProps {
  rows: UserRow[];
  loading: boolean;
  agentsByUserId: Map<number, OctopAgent[]>;
  agentsLoading: boolean;
  currentUserId: number | null;
  permLabelByKey: Map<string, string>;
  orgUnitLabelByKey: Map<string, string>;
  onTogglePatch: (
    row: UserRow,
    patch: Partial<Pick<UserRow, "role" | "disabled" | "permissions">>,
  ) => Promise<boolean>;
  onEdit: (row: UserRow) => void;
  onShowAgents: (row: UserRow) => void;
  onResetPassword: (row: UserRow) => void;
  onDelete: (row: UserRow) => Promise<void>;
  onUnlockLogin: (row: UserRow) => Promise<void>;
  /** Admin-only affordances (enable toggle, reset, delete) are hidden without it. */
  admin: boolean;
  nowSec: number;
}

function userInitials(displayName: string, username: string): string {
  const source = displayName.trim() || username;
  const parts = source.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return source.slice(0, 2).toUpperCase();
}

const FIELD_ICON_PROPS = {
  size: 16 as const,
  style: { color: "var(--fn-text-tertiary)" },
};

function policyPayload(
  values: PolicyFormValues,
  options: { workspaceRootAllowed: boolean },
): {
  workspace_root_dir: string | null;
  token_quota: number | null;
} {
  return {
    workspace_root_dir:
      options.workspaceRootAllowed && values.limit_workspace_root
        ? values.workspace_root_dir?.trim() || null
        : null,
    token_quota: values.limit_token_quota ? values.token_quota ?? null : null,
  };
}

function ResourcePolicyFields({
  fsTreeRoot,
  workspaceRootAllowed,
}: {
  fsTreeRoot: string;
  workspaceRootAllowed: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className={`${styles.createSection} ${styles.policySection}`}>
      <div className={styles.createSectionTitle}>
        {t("adminUsers.createSectionPolicy")}
      </div>
      <Form.Item
        label={t("adminUsers.policyWorkspaceRoot")}
        extra={
          workspaceRootAllowed
            ? t("adminUsers.policyWorkspaceRootHint", {
                localShell: t("experts.backendModes.localShell"),
                filesystem: t("experts.backendModes.filesystem"),
              })
            : t("adminUsers.policyWorkspaceRootContainerHint")
        }
      >
        <Form.Item name="limit_workspace_root" valuePropName="checked" noStyle>
          <Switch disabled={!workspaceRootAllowed} />
        </Form.Item>
      </Form.Item>
      {workspaceRootAllowed ? (
        <Form.Item
          noStyle
          shouldUpdate={(prev, cur) =>
            prev.limit_workspace_root !== cur.limit_workspace_root
          }
        >
          {({ getFieldValue }) =>
            getFieldValue("limit_workspace_root") ? (
              <Form.Item
                name="workspace_root_dir"
                rules={[
                  {
                    required: true,
                    message: t("adminUsers.policyWorkspaceRootRequired"),
                  },
                ]}
              >
                <RootDirSelect treeRoot={fsTreeRoot} />
              </Form.Item>
            ) : null
          }
        </Form.Item>
      ) : null}
      <Form.Item
        label={t("adminUsers.policyTokenQuota")}
        extra={t("adminUsers.policyTokenQuotaHint")}
      >
        <Form.Item name="limit_token_quota" valuePropName="checked" noStyle>
          <Switch />
        </Form.Item>
      </Form.Item>
      <Form.Item
        noStyle
        shouldUpdate={(prev, cur) =>
          prev.limit_token_quota !== cur.limit_token_quota
        }
      >
        {({ getFieldValue }) =>
          getFieldValue("limit_token_quota") ? (
            <Form.Item
              name="token_quota"
              rules={[
                {
                  required: true,
                  message: t("adminUsers.policyTokenQuotaRequired"),
                },
              ]}
            >
              <InputNumber min={0} step={1000} style={{ width: "100%" }} />
            </Form.Item>
          ) : null
        }
      </Form.Item>
    </div>
  );
}

interface RolePickerProps {
  value?: OctopRole;
  onChange?: (value: OctopRole) => void;
  disabled?: boolean;
  options: {
    value: OctopRole;
    label: string;
    hint: string;
  }[];
}

function RolePicker({ value, onChange, options, disabled }: RolePickerProps) {
  return (
    <div className={styles.rolePicker} role="radiogroup">
      {options.map((opt) => {
        const selected = value === opt.value;
        const Icon = ROLE_ICONS[opt.value];
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            className={`${styles.roleOption} ${
              selected ? styles.roleOptionSelected : ""
            }`}
            onClick={() => {
              if (!disabled) onChange?.(opt.value);
            }}
          >
            <span className={styles.roleOptionIcon} aria-hidden>
              <Icon size={15} strokeWidth={2} />
            </span>
            <span className={styles.roleOptionBody}>
              <span className={styles.roleOptionLabel}>{opt.label}</span>
              <span className={styles.roleOptionHint}>{opt.hint}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

/**
 * Org-unit scope picker for non-admin roles. The unit drives the resource
 * boundary (and its module grants); leaving it empty means no unit scope.
 */
function OrgUnitField({
  options,
}: {
  options: { value: string; label: string }[];
}) {
  const { t } = useTranslation();
  return (
    <Form.Item
      label={
        <span className={styles.orgUnitLabel}>
          <Building2 size={14} strokeWidth={2} aria-hidden />
          {t("adminUsers.orgUnit")}
        </span>
      }
      name="org_unit"
      extra={t("adminUsers.orgUnitHint")}
    >
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder={t("adminUsers.orgUnitPlaceholder")}
        notFoundContent={t("adminUsers.orgUnitEmpty")}
        options={options}
      />
    </Form.Item>
  );
}

/** One category of the permission catalog, split into page groups. */
interface PermissionGroup {
  category: string;
  label: string;
  items: PermissionCatalogItem[];
  /** Keys belonging to no page: rendered above the page boxes. */
  standalone: PermissionCatalogItem[];
  pages: { page: string; label: string; items: PermissionCatalogItem[] }[];
}

/**
 * Category → page grouping of the catalog. Shared by the grant and the deny
 * picker so both show the same catalog, in the same order and shape.
 */
function groupPermissionCatalog(
  catalog: PermissionCatalogItem[],
  labels: { settings: string; control: string; admin: string },
): PermissionGroup[] {
  const order = [
    { category: "settings", label: labels.settings },
    { category: "control", label: labels.control },
    { category: "admin", label: labels.admin },
  ];
  return order
    .map((g) => {
      const items = catalog.filter((p) => p.category === g.category);
      const pages: PermissionGroup["pages"] = [];
      const standalone: PermissionCatalogItem[] = [];
      for (const item of items) {
        if (!item.page) {
          standalone.push(item);
          continue;
        }
        const existing = pages.find((p) => p.page === item.page);
        if (existing) {
          existing.items.push(item);
        } else {
          pages.push({
            page: item.page,
            label: item.page_label || item.page,
            items: [item],
          });
        }
      }
      return { ...g, items, standalone, pages };
    })
    .filter((g) => g.items.length > 0);
}

/** Why a chip's own state is not the whole story for one catalog key. */
type ChipTag = "own" | "unit" | "deny";

/**
 * Provenance markers shown on a chip. Both pickers pass only the sources that
 * add information there: the deny picker marks what a deny would take away
 * (granted directly / by the department), the grant picker marks the two
 * reasons a tick or an untick would not decide the outcome (a department
 * grant, and the deny list, which outranks everything).
 */
function chipSources(
  key: string,
  sources: {
    ownKeys?: Set<string>;
    unitGrantedKeys?: Set<string>;
    deniedKeys?: Set<string>;
  },
): ChipTag[] {
  const tags: ChipTag[] = [];
  if (sources.ownKeys?.has(key)) tags.push("own");
  if (sources.unitGrantedKeys?.has(key)) tags.push("unit");
  if (sources.deniedKeys?.has(key)) tags.push("deny");
  return tags;
}

const CHIP_TAG_LABELS: Record<ChipTag, string> = {
  own: "adminUsers.permSourceOwn",
  unit: "adminUsers.permSourceUnit",
  deny: "adminUsers.permDenyBadge",
};

function ChipTagList({ tags }: { tags: ChipTag[] }) {
  const { t } = useTranslation();
  if (tags.length === 0) return null;
  return (
    <span className={styles.permChipTags}>
      {tags.map((tag) => (
        <span
          key={tag}
          className={`${styles.permChipTag} ${
            tag === "deny" ? styles.permChipTagDenied : ""
          }`}
        >
          {t(CHIP_TAG_LABELS[tag])}
        </span>
      ))}
    </span>
  );
}

interface PermissionCheckboxPickerProps {
  value?: string[];
  onChange?: (value: string[]) => void;
  catalog: PermissionCatalogItem[];
  disabled?: boolean;
  /** Keys the account's org unit grants: a cleared box does not remove them. */
  unitGrantedKeys?: Set<string>;
  /** Keys on the deny list: a ticked box does not turn them on. */
  deniedKeys?: Set<string>;
}

function PermissionCheckboxPicker({
  value,
  onChange,
  catalog,
  disabled,
  unitGrantedKeys,
  deniedKeys,
}: PermissionCheckboxPickerProps) {
  const { t } = useTranslation();
  const selected = value ?? [];
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const groups = useMemo(
    () =>
      groupPermissionCatalog(catalog, {
        settings: t("adminUsers.permGroupSettings"),
        control: t("adminUsers.permGroupControl"),
        admin: t("adminUsers.permGroupAdmin"),
      }),
    [catalog, t],
  );

  const toggle = (key: string, checked: boolean) => {
    if (disabled) return;
    if (checked) {
      onChange?.([...selected, key]);
      return;
    }
    onChange?.(selected.filter((k) => k !== key));
  };

  const setGroup = (keys: string[], checked: boolean) => {
    if (disabled) return;
    if (checked) {
      const next = new Set(selected);
      for (const k of keys) next.add(k);
      onChange?.(Array.from(next));
      return;
    }
    const drop = new Set(keys);
    onChange?.(selected.filter((k) => !drop.has(k)));
  };

  if (catalog.length === 0) {
    return (
      <div className={styles.permEmpty}>
        <Text type="secondary">{t("adminUsers.permCatalogEmpty")}</Text>
      </div>
    );
  }

  return (
    <div
      className={`${styles.permPicker} ${
        disabled ? styles.permPickerDisabled : ""
      }`}
    >
      {groups.map((group) => {
        const keys = group.items.map((i) => i.key);
        const checkedCount = keys.filter((k) => selectedSet.has(k)).length;
        const allChecked = checkedCount === keys.length && keys.length > 0;
        const indeterminate = checkedCount > 0 && !allChecked;
        const renderChips = (items: PermissionCatalogItem[]) => (
          <div className={styles.permGrid} role="group">
            {items.map((item) => {
              const checked = selectedSet.has(item.key);
              const tags = chipSources(item.key, {
                unitGrantedKeys,
                deniedKeys,
              });
              // Explains the tick that lies: a department grant survives an
              // untick, a deny survives a tick.
              const title = tags.includes("deny")
                ? t("adminUsers.permGrantDeniedNote")
                : tags.includes("unit")
                  ? t("adminUsers.permGrantUnitNote")
                  : undefined;
              return (
                <button
                  key={`${item.key}:${item.label}`}
                  type="button"
                  disabled={disabled}
                  aria-pressed={checked}
                  title={title}
                  className={`${styles.permChip} ${
                    checked ? styles.permChipSelected : ""
                  }`}
                  onClick={() => toggle(item.key, !checked)}
                >
                  <span className={styles.permChipCheck} aria-hidden>
                    {checked ? <Check size={12} strokeWidth={2.5} /> : null}
                  </span>
                  <span className={styles.permChipLabel}>{item.label}</span>
                  <ChipTagList tags={tags} />
                </button>
              );
            })}
          </div>
        );
        return (
          <section key={group.category} className={styles.permGroup}>
            <div className={styles.permGroupHeader}>
              <Checkbox
                checked={allChecked}
                indeterminate={indeterminate}
                disabled={disabled}
                onChange={(e) => setGroup(keys, e.target.checked)}
              >
                <span className={styles.permGroupTitle}>{group.label}</span>
              </Checkbox>
              <span className={styles.permGroupCount}>
                {checkedCount}/{keys.length}
              </span>
            </div>
            {group.pages.length === 0 ? (
              renderChips(group.items)
            ) : (
              <>
                {group.standalone.length > 0
                  ? renderChips(group.standalone)
                  : null}
                {group.pages.map((page) => {
                  const pageKeys = page.items.map((i) => i.key);
                  const pageChecked = pageKeys.filter((k) =>
                    selectedSet.has(k),
                  ).length;
                  const pageAll =
                    pageChecked === pageKeys.length && pageKeys.length > 0;
                  const pageIndeterminate = pageChecked > 0 && !pageAll;
                  return (
                    <div key={page.page} className={styles.permPage}>
                      <div className={styles.permPageHeader}>
                        <Checkbox
                          checked={pageAll}
                          indeterminate={pageIndeterminate}
                          disabled={disabled}
                          onChange={(e) => setGroup(pageKeys, e.target.checked)}
                        >
                          <span className={styles.permPageTitle}>
                            {page.label}
                          </span>
                        </Checkbox>
                        <span className={styles.permGroupCount}>
                          {pageChecked}/{pageKeys.length}
                        </span>
                      </div>
                      {renderChips(page.items)}
                    </div>
                  );
                })}
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}

interface PermissionDenyPickerProps {
  value?: string[];
  onChange?: (value: string[]) => void;
  catalog: PermissionCatalogItem[];
  /** Keys granted to this account itself — a deny here revokes a real grant. */
  grantedKeys: Set<string>;
  /** Keys the account's department grants — deny is the per-user way out. */
  unitGrantedKeys: Set<string>;
}

/**
 * The deny leg of ``role ∪ unit ∪ grant − deny``: same catalog and grouping as
 * the grant picker, red instead of brand-coloured. Each selected chip names
 * what the deny actually takes away, because a key can be granted to this
 * account directly, by its department, or by nothing at all — and in the last
 * case the deny is still meaningful: it is a standing rule that keeps every
 * later grant of that key from taking effect.
 */
function PermissionDenyPicker({
  value,
  onChange,
  catalog,
  grantedKeys,
  unitGrantedKeys,
}: PermissionDenyPickerProps) {
  const { t } = useTranslation();
  const denied = value ?? [];
  const deniedSet = useMemo(() => new Set(denied), [denied]);

  const groups = useMemo(
    () =>
      groupPermissionCatalog(catalog, {
        settings: t("adminUsers.permGroupSettings"),
        control: t("adminUsers.permGroupControl"),
        admin: t("adminUsers.permGroupAdmin"),
      }),
    [catalog, t],
  );

  const toggle = (key: string, isDenied: boolean) => {
    if (isDenied) {
      onChange?.(denied.filter((k) => k !== key));
      return;
    }
    onChange?.([...denied, key]);
  };

  if (catalog.length === 0) {
    return (
      <div className={styles.permEmpty}>
        <Text type="secondary">{t("adminUsers.permCatalogEmpty")}</Text>
      </div>
    );
  }

  const deniedTotal = catalog.filter((p) => deniedSet.has(p.key)).length;

  return (
    <div className={styles.permPicker}>
      {deniedTotal > 0 ? (
        <div className={styles.permDenyCount}>
          <ShieldOff size={14} strokeWidth={2} />
          <span>{t("adminUsers.permDenyCount", { count: deniedTotal })}</span>
        </div>
      ) : null}
      {groups.map((group) => {
        const keys = group.items.map((i) => i.key);
        const deniedCount = keys.filter((k) => deniedSet.has(k)).length;
        const renderChips = (items: PermissionCatalogItem[]) => (
          <div className={styles.permGrid} role="group">
            {items.map((item) => {
              const isDenied = deniedSet.has(item.key);
              const tags = chipSources(item.key, {
                ownKeys: grantedKeys,
                unitGrantedKeys,
              });
              const title = isDenied
                ? tags.includes("unit")
                  ? t("adminUsers.permDenyUnitNote")
                  : tags.includes("own")
                    ? t("adminUsers.permDenyGrantedNote")
                    : t("adminUsers.permDenyAbsentNote")
                : undefined;
              return (
                <button
                  key={`${item.key}:${item.label}`}
                  type="button"
                  aria-pressed={isDenied}
                  title={title}
                  className={`${styles.permChip} ${
                    isDenied ? styles.permChipDenied : ""
                  }`}
                  onClick={() => toggle(item.key, isDenied)}
                >
                  <span className={styles.permChipCheck} aria-hidden>
                    {isDenied ? <Ban size={12} strokeWidth={2.5} /> : null}
                  </span>
                  <span className={styles.permChipLabel}>{item.label}</span>
                  <ChipTagList tags={tags} />
                </button>
              );
            })}
          </div>
        );
        return (
          <section key={group.category} className={styles.permGroup}>
            <div className={styles.permGroupHeader}>
              <span className={styles.permGroupTitle}>{group.label}</span>
              <span className={styles.permGroupCount}>
                {deniedCount}/{keys.length}
              </span>
            </div>
            {group.pages.length === 0 ? (
              renderChips(group.items)
            ) : (
              <>
                {group.standalone.length > 0
                  ? renderChips(group.standalone)
                  : null}
                {group.pages.map((page) => {
                  const pageDenied = page.items.filter((i) =>
                    deniedSet.has(i.key),
                  ).length;
                  return (
                    <div key={page.page} className={styles.permPage}>
                      <div className={styles.permPageHeader}>
                        <span className={styles.permPageTitle}>
                          {page.label}
                        </span>
                        <span className={styles.permGroupCount}>
                          {pageDenied}/{page.items.length}
                        </span>
                      </div>
                      {renderChips(page.items)}
                    </div>
                  );
                })}
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}

function PermissionSummary({
  row,
  permLabelByKey,
}: {
  row: UserRow;
  permLabelByKey: Map<string, string>;
}) {
  const { t } = useTranslation();
  if (row.role === "admin") {
    return (
      <span className={`${styles.permBadge} ${styles.permBadgeAll}`}>
        {t("adminUsers.permAll")}
      </span>
    );
  }
  const keys = row.permissions ?? [];
  if (keys.length === 0) {
    return <span className={styles.permBadgeMuted}>—</span>;
  }
  const names = keys.map((key) => permLabelByKey.get(key) ?? key);
  return (
    <Tooltip title={names.join("、")}>
      <span className={styles.permBadge}>
        {t("adminUsers.permCount", { count: keys.length })}
      </span>
    </Tooltip>
  );
}

function RoleLegend() {
  const { t } = useTranslation();
  return (
    <div className={styles.roleLegend} role="note">
      <span className={styles.roleLegendLabel}>
        {t("adminUsers.roleLegendTitle")}
      </span>
      <p className={styles.roleLegendText}>{t("adminUsers.roleLegend")}</p>
    </div>
  );
}

function UserCardGrid({
  rows,
  loading,
  agentsByUserId,
  agentsLoading,
  currentUserId,
  permLabelByKey,
  orgUnitLabelByKey,
  onTogglePatch,
  onEdit,
  onShowAgents,
  onResetPassword,
  onDelete,
  onUnlockLogin,
  admin,
  nowSec,
}: UserCardGridProps) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  if (loading && rows.length === 0) {
    return (
      <div className={styles.userGridLoading}>
        <Spin />
      </div>
    );
  }
  if (rows.length === 0) {
    return <Empty description={t("adminUsers.noUsers")} />;
  }
  return (
    <div className={styles.userCardGrid}>
      {rows.map((row) => {
        const agentCount = agentsByUserId.get(row.id)?.length ?? 0;
        const isSelf = row.id === currentUserId;
        const displayName = row.display_name?.trim() || row.username;
        const remaining = lockRemainingSeconds(row, nowSec);
        const isLocked = remaining > 0;
        const failedCount = row.login_failed_count ?? 0;
        const accentClass = isLocked
          ? styles.userCardAccentLocked
          : row.disabled
          ? styles.userCardAccentDisabled
          : row.role === "admin"
          ? styles.userCardAccentAdmin
          : styles.userCardAccentUser;
        const statusColor = row.disabled ? "#8c8c8c" : "#52c41a";
        const statusBg = row.disabled
          ? "rgba(140,140,140,0.10)"
          : "rgba(82,196,26,0.10)";
        return (
          <div
            key={row.id}
            className={[
              styles.userCard,
              isLocked ? styles.userCardLocked : "",
              row.disabled ? styles.userCardDisabled : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <div className={`${styles.userCardAccent} ${accentClass}`} />

            <div className={styles.userCardInner}>
              <div className={styles.userCardHeader}>
                <div
                  className={`${styles.userCardAvatar} ${roleToneClass(
                    row.role,
                  )}`}
                  aria-hidden="true"
                >
                  {userInitials(displayName, row.username)}
                </div>

                <div className={styles.userCardTitleBlock}>
                  <div className={styles.userCardNameRow}>
                    <span className={styles.userCardName}>{displayName}</span>
                    {isSelf && (
                      <Tag className={styles.userCardYouTag}>
                        {t("adminUsers.you")}
                      </Tag>
                    )}
                  </div>
                  <div className={styles.userCardHandle}>@{row.username}</div>
                </div>

                {admin && (
                  <Switch
                    size="small"
                    checked={!row.disabled}
                    onChange={(checked) =>
                      void onTogglePatch(row, { disabled: !checked })
                    }
                    className={styles.userCardSwitch}
                    aria-label={t("common.enabled")}
                  />
                )}
              </div>

              <div className={styles.userCardMeta}>
                <span
                  className={`${styles.userCardPill} ${roleToneClass(
                    row.role,
                  )}`}
                >
                  {t(ROLE_LABEL_KEYS[row.role])}
                </span>
                {row.org_unit && (
                  <span className={styles.userCardPill}>
                    {orgUnitLabelByKey.get(row.org_unit) ?? row.org_unit}
                  </span>
                )}
                <span
                  className={styles.userCardPill}
                  style={{ color: statusColor, background: statusBg }}
                >
                  <span
                    className={styles.userCardStatusDot}
                    style={{ background: statusColor }}
                  />
                  {row.disabled
                    ? t("adminUsers.statusDisabled")
                    : t("adminUsers.statusEnabled")}
                </span>
                {row.sso_linked && (
                  <span className={styles.userCardAuth}>
                    {t("adminUsers.ssoBadge")}
                  </span>
                )}
                {row.has_password && (
                  <span className={styles.userCardAuth}>
                    {t("adminUsers.passwordBadge")}
                  </span>
                )}
              </div>

              <div className={styles.userCardInfo}>
                <span className={styles.userCardTime}>
                  <Mail size={11} />
                  <span>{row.email?.trim() || "—"}</span>
                </span>
                {row.created_at != null && (
                  <Tooltip title={t("adminUsers.colCreatedAt")}>
                    <span className={styles.userCardTime}>
                      <Clock size={11} />
                      <span>{formatUserTs(row.created_at, timeZone)}</span>
                    </span>
                  </Tooltip>
                )}
                <PermissionSummary row={row} permLabelByKey={permLabelByKey} />
              </div>

              <div className={styles.userCardStats}>
                <button
                  type="button"
                  className={styles.userCardStatBtn}
                  onClick={() => onShowAgents(row)}
                >
                  <Bot size={15} />
                  <span>{t("adminUsers.colAgents")}</span>
                  <span className={styles.userCardStatCount}>
                    {agentsLoading ? "…" : agentCount}
                  </span>
                  <ChevronRight size={14} />
                </button>
              </div>

              {isLocked && (
                <div className={styles.userCardLockAlert}>
                  <Lock size={14} />
                  <span className={styles.userCardLockText}>
                    {t("adminUsers.loginLockActive", {
                      minutes: Math.max(1, Math.ceil(remaining / 60)),
                    })}
                  </span>
                  <Button
                    type="link"
                    size="small"
                    className={styles.userCardLockUnlock}
                    onClick={() => void onUnlockLogin(row)}
                  >
                    {t("adminUsers.unlockLogin")}
                  </Button>
                </div>
              )}

              {!isLocked && failedCount > 0 && (
                <div className={styles.userCardFailedHint}>
                  {t("adminUsers.loginFailedCount", { count: failedCount })}
                </div>
              )}

              <div className={styles.userCardFooter}>
                <Tooltip title={t("common.edit")} mouseEnterDelay={0.5}>
                  <button
                    type="button"
                    className={styles.userCardIconBtn}
                    onClick={() => onEdit(row)}
                    aria-label={t("common.edit")}
                  >
                    <Pencil size={15} />
                  </button>
                </Tooltip>

                {admin && (
                  <Tooltip
                    title={t("adminUsers.resetPassword")}
                    mouseEnterDelay={0.5}
                  >
                    <button
                      type="button"
                      className={styles.userCardIconBtn}
                      onClick={() => onResetPassword(row)}
                      aria-label={t("adminUsers.resetPassword")}
                    >
                      <KeyRound size={15} />
                    </button>
                  </Tooltip>
                )}

                {admin && (
                  <Popconfirm
                    title={t("adminUsers.deleteConfirm", {
                      username: row.username,
                    })}
                    onConfirm={() => void onDelete(row)}
                    disabled={isSelf}
                  >
                    <Tooltip
                      title={
                        isSelf ? t("adminUsers.deleteSelf") : t("common.delete")
                      }
                      mouseEnterDelay={0.5}
                    >
                      <button
                        type="button"
                        className={`${styles.userCardIconBtn} ${styles.userCardIconBtnDanger}`}
                        disabled={isSelf}
                        aria-label={t("common.delete")}
                      >
                        <Trash2 size={15} />
                      </button>
                    </Tooltip>
                  </Popconfirm>
                )}

                <span className={styles.userCardFooterSpacer} />

                <span className={styles.userCardIdBadge}>#{row.id}</span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Compact login-lock status indicator. Used by both the card view
 * (inline in a `userCard2Row`) and the table view (table cell).
 */
function UserLoginLock({
  row,
  nowSec,
  onUnlock,
}: {
  row: UserRow;
  nowSec: number;
  onUnlock: () => void;
}) {
  const { t } = useTranslation();
  const failedCount = row.login_failed_count ?? 0;
  if (!row.login_locked) {
    if (failedCount > 0) {
      return (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t("adminUsers.loginFailedCount", { count: failedCount })}
        </Text>
      );
    }
    return (
      <Text type="secondary" style={{ fontSize: 12 }}>
        {t("adminUsers.loginLockNone")}
      </Text>
    );
  }
  const remaining = lockRemainingSeconds(row, nowSec);
  const minutes = Math.max(1, Math.ceil(remaining / 60));
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        flexWrap: "wrap",
        justifyContent: "flex-end",
      }}
    >
      <Tag
        color="error"
        style={{ margin: 0, fontSize: 11, lineHeight: "18px" }}
      >
        {t("adminUsers.loginLockActive", { minutes })}
      </Tag>
      <Button
        size="small"
        type="link"
        onClick={onUnlock}
        style={{ padding: 0, fontSize: 12, height: "auto" }}
      >
        {t("adminUsers.unlockLogin")}
      </Button>
    </span>
  );
}

export default function UsersListPanel() {
  const { t, i18n } = useTranslation();
  const timeZone = useServerTimezone();
  const isMobile = useIsMobile();
  const currentUser = useCurrentUser();
  const admin = isSystemAdmin(currentUser);
  const [agents, setAgents] = useState<OctopAgent[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [rows, setRows] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<CreateValues>();
  const [editTarget, setEditTarget] = useState<UserRow | null>(null);
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [editForm] = Form.useForm<EditValues>();
  const [resetTarget, setResetTarget] = useState<UserRow | null>(null);
  const [resetSubmitting, setResetSubmitting] = useState(false);
  const [resetForm] = Form.useForm<ResetValues>();
  const [currentUserId, setCurrentUserId] = useState<number | null>(null);
  const [agentDrawerUser, setAgentDrawerUser] = useState<UserRow | null>(null);
  const [editAgent, setEditAgent] = useState<OctopAgent | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const { viewMode, setViewMode, showCardView } = useCardTableView("table");
  const [permCatalog, setPermCatalog] = useState<PermissionCatalogItem[]>([]);
  /** Module keys the department in the edit drawer grants its members. */
  const [editUnitGrants, setEditUnitGrants] = useState<string[]>([]);
  /**
   * Whether the admin touched the deny picker. ``denied_permissions`` is
   * three-state on the wire (omitted = keep, ``null`` = clear, array = set),
   * so an untouched drawer omits it instead of rewriting the stored list.
   */
  const [denyTouched, setDenyTouched] = useState(false);
  const unitGrantRequest = useRef<string | null>(null);
  const [orgUnits, setOrgUnits] = useState<OrgUnit[]>([]);
  const [fsTreeRoot, setFsTreeRoot] = useState(HOST_FS_ROOT);
  const [workspaceRootAllowed, setWorkspaceRootAllowed] = useState(true);

  const permLabelByKey = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of permCatalog) {
      map.set(item.key, permFullLabel(item));
    }
    return map;
  }, [permCatalog]);

  /** Short labels for the "deny wins" list, where full paths would bury it. */
  const permShortLabelByKey = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of permCatalog) {
      map.set(item.key, item.label);
    }
    return map;
  }, [permCatalog]);

  const unitGrantedKeys = useMemo(
    () => new Set(editUnitGrants),
    [editUnitGrants],
  );

  /**
   * Module keys this actor may hand out. The backend (``_assert_can_assign``)
   * compares the whole submitted list against the actor's own keys, so a
   * non-admin must not even be offered a key it does not hold — picking one
   * would 403 the request it rides on.
   */
  const assignableCatalog = useMemo(() => {
    if (admin) return permCatalog;
    const held = new Set(currentUser?.permissions ?? []);
    return permCatalog.filter((p) => held.has(p.key));
  }, [admin, permCatalog, currentUser]);

  const baselinePermissions = useMemo(
    () =>
      assignableCatalog
        .filter((p) => p.category === "settings")
        .map((p) => p.key),
    [assignableCatalog],
  );

  const lang = normalizeUiLocale(i18n.language);

  const orgUnitLabelByKey = useMemo(() => {
    const map = new Map<string, string>();
    for (const unit of orgUnits) {
      map.set(unit.key, pickLocale(unit.label, lang) || unit.key);
    }
    return map;
  }, [orgUnits, lang]);

  /** Select options; nested units are shown as "Parent / Child". */
  const orgUnitOptions = useMemo(
    () =>
      orgUnits.map((unit) => {
        const label = orgUnitLabelByKey.get(unit.key) ?? unit.key;
        const parentLabel = unit.parent_key
          ? orgUnitLabelByKey.get(unit.parent_key)
          : undefined;
        return {
          value: unit.key,
          label: parentLabel ? `${parentLabel} / ${label}` : label,
        };
      }),
    [orgUnits, orgUnitLabelByKey],
  );

  const createRoleOptions = useMemo(
    () => [
      {
        value: "user" as const,
        label: t("adminUsers.roleUser"),
        hint: t("adminUsers.roleUserHint"),
      },
      {
        value: "unit_admin" as const,
        label: t("adminUsers.roleUnitAdmin"),
        hint: t("adminUsers.roleUnitAdminHint"),
      },
      {
        value: "admin" as const,
        label: t("adminUsers.roleAdmin"),
        hint: t("adminUsers.roleAdminHint"),
      },
    ],
    [t],
  );

  const isSelfAdmin = useCallback(
    (row: UserRow) => row.id === currentUserId && row.role === "admin",
    [currentUserId],
  );

  /**
   * Whether the edit drawer may submit ``permissions`` for the target on
   * screen. Admins grant anything; a non-admin grants only keys it holds, and
   * ``_assert_can_assign`` rejects the whole list on a single foreign key — so
   * a target carrying keys the actor lacks is saved without the field
   * (omitted = untouched on both sides) instead of by a request that would
   * 403 and take the display-name edit down with it.
   */
  const canSubmitPermissions = useMemo(() => {
    if (admin) return true;
    if (!editTarget || editTarget.role === "admin") return false;
    const held = new Set(currentUser?.permissions ?? []);
    return (editTarget.permissions ?? []).every((key) => held.has(key));
  }, [admin, editTarget, currentUser]);

  const hasLockedUser = useMemo(
    () => rows.some((row) => row.login_locked),
    [rows],
  );
  const nowSec = useNowSeconds(hasLockedUser);

  const agentsByUserId = useMemo(() => {
    const map = new Map<number, OctopAgent[]>();
    for (const agent of agents) {
      if (agent.user_id == null) continue;
      const list = map.get(agent.user_id) ?? [];
      list.push(agent);
      map.set(agent.user_id, list);
    }
    return map;
  }, [agents]);

  const drawerAgents = agentDrawerUser
    ? agentsByUserId.get(agentDrawerUser.id) ?? []
    : [];

  const filteredRows = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return rows;
    return rows.filter((row) => {
      const username = row.username.toLowerCase();
      const displayName = (row.display_name ?? "").trim().toLowerCase();
      const email = (row.email ?? "").trim().toLowerCase();
      return (
        username.includes(query) ||
        displayName.includes(query) ||
        email.includes(query)
      );
    });
  }, [rows, searchQuery]);

  const refreshUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<UserRow[]>("/users");
      setRows(data);
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : t("adminUsers.loadFailed"),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (!hasLockedUser) return;
    const anyExpired = rows.some(
      (row) => row.login_locked && lockRemainingSeconds(row, nowSec) === 0,
    );
    if (anyExpired) void refreshUsers();
  }, [hasLockedUser, nowSec, rows, refreshUsers]);

  const refreshAgents = useCallback(async () => {
    setAgentsLoading(true);
    try {
      const data = await request<OctopAgent[]>("/agents?scope=all");
      setAgents(data);
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : t("adminUsers.loadFailed"),
      );
      setAgents([]);
    } finally {
      setAgentsLoading(false);
    }
  }, [t]);

  const patchAgent = useCallback(
    (agentId: string, patch: Partial<OctopAgent>) => {
      setAgents((prev) =>
        prev.map((a) => (a.agent_id === agentId ? { ...a, ...patch } : a)),
      );
    },
    [],
  );

  const handleDrawerStateChange = useCallback(
    (agentId: string, newState: string) => {
      patchAgent(agentId, { state: newState });
    },
    [patchAgent],
  );

  const handleDrawerDeleted = useCallback(
    (agentId: string) => {
      setAgents((prev) => prev.filter((a) => a.agent_id !== agentId));
      void refreshAgents();
    },
    [refreshAgents],
  );

  const handleEditSaved = useCallback(
    (
      updated: Pick<
        OctopAgent,
        "agent_id" | "name" | "description" | "default_model"
      >,
    ) => {
      setEditAgent(null);
      patchAgent(updated.agent_id, {
        name: updated.name,
        description: updated.description,
        default_model: updated.default_model,
      });
      void refreshAgents();
    },
    [patchAgent, refreshAgents],
  );

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshUsers(), refreshAgents()]);
  }, [refreshUsers, refreshAgents]);

  useEffect(() => {
    void refreshAll();
    authApi
      .me()
      .then((u) => setCurrentUserId(u.id))
      .catch(() => setCurrentUserId(null));
    request<PermissionCatalogItem[]>("/users/permissions")
      .then(setPermCatalog)
      .catch(() => setPermCatalog([]));
    fetchOrgUnits()
      .then(setOrgUnits)
      .catch(() => setOrgUnits([]));
  }, [refreshAll]);

  // ``/filesystem/defaults`` is admin-only: without it a non-admin operator
  // keeps the root-dir policy locked instead of firing a 403.
  useEffect(() => {
    if (!admin) {
      setFsTreeRoot(HOST_FS_ROOT);
      setWorkspaceRootAllowed(false);
      return;
    }
    fetchFilesystemDefaults()
      .then((defaults) => {
        setFsTreeRoot(defaults.tree_root);
        setWorkspaceRootAllowed(!defaults.in_container);
      })
      .catch(() => {
        setFsTreeRoot(HOST_FS_ROOT);
        setWorkspaceRootAllowed(true);
      });
  }, [admin]);

  const onCreate = async (values: CreateValues) => {
    setSubmitting(true);
    try {
      await request("/users", {
        method: "POST",
        body: JSON.stringify({
          username: values.username,
          display_name: values.display_name?.trim() || null,
          email: values.email?.trim() || null,
          password: values.password,
          // The create picker is admin-only (``_assert_admin(actor, "create a
          // non-user account")``), so a non-admin mints plain accounts: the
          // field is not registered for them and the role is pinned here.
          role: admin ? values.role : "user",
          org_unit: !admin || values.role === "admin" ? null : values.org_unit ?? null,
          permissions:
            admin && values.role === "admin" ? [] : values.permissions ?? [],
          ...policyPayload(values, { workspaceRootAllowed }),
        }),
      });
      message.success(
        t("adminUsers.createSuccess", { username: values.username }),
      );
      form.resetFields();
      setCreateOpen(false);
      void refreshUsers();
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : t("adminUsers.createFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const openCreate = () => {
    form.setFieldsValue({
      role: "user",
      permissions: [...baselinePermissions],
      username: undefined,
      display_name: undefined,
      email: undefined,
      password: undefined,
      confirm: undefined,
      org_unit: undefined,
      limit_workspace_root: false,
      workspace_root_dir: undefined,
      limit_token_quota: false,
      token_quota: undefined,
    });
    setCreateOpen(true);
  };

  /**
   * The edit drawer's department grants — what explains a permission the
   * account holds without a tick of its own, and what a deny would take away.
   * Reloaded whenever the drawer's department changes, and dropped on a stale
   * response so a slow request cannot label the next account.
   */
  const loadUnitGrants = useCallback((unitKey: string | null | undefined) => {
    const key = unitKey ?? null;
    unitGrantRequest.current = key;
    if (!key) {
      setEditUnitGrants([]);
      return;
    }
    request<{ unit_key: string; permissions: string[] }>(
      `/org-units/${encodeURIComponent(key)}/permissions`,
    )
      .then((res) => {
        if (unitGrantRequest.current !== key) return;
        setEditUnitGrants(res.permissions ?? []);
      })
      .catch(() => {
        if (unitGrantRequest.current !== key) return;
        setEditUnitGrants([]);
      });
  }, []);

  const openEdit = (row: UserRow) => {
    setEditTarget(row);
    setDenyTouched(false);
    loadUnitGrants(row.org_unit);
    editForm.setFieldsValue({
      display_name: row.display_name ?? "",
      email: row.email ?? "",
      role: row.role,
      org_unit: row.org_unit ?? undefined,
      permissions: [...(row.permissions ?? [])],
      denied_permissions: [...(row.denied_permissions ?? [])],
      limit_workspace_root: workspaceRootAllowed
        ? Boolean(row.workspace_root_dir)
        : false,
      workspace_root_dir: workspaceRootAllowed
        ? row.workspace_root_dir ?? undefined
        : undefined,
      limit_token_quota: row.token_quota != null,
      token_quota: row.token_quota ?? undefined,
    });
  };

  const togglePatch = async (
    row: UserRow,
    patch: Partial<Pick<UserRow, "role" | "disabled" | "permissions">>,
  ): Promise<boolean> => {
    if (
      patch.role !== undefined &&
      patch.role !== "admin" &&
      row.id === currentUserId &&
      row.role === "admin"
    ) {
      message.warning(t("adminUsers.demoteSelf"));
      return false;
    }
    try {
      await request(`/users/${row.id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      void refreshUsers();
      return true;
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : t("adminUsers.updateFailed"),
      );
      return false;
    }
  };

  const onEditSubmit = async (values: EditValues) => {
    if (!editTarget) return;
    setEditSubmitting(true);
    // Role, department and module keys carry their own gates on the backend,
    // and it refuses the *whole* PATCH over one forbidden field — so a field
    // this actor may not write is left out rather than sent and rejected
    // (omitted = keep the stored value).
    const { workspace_root_dir, token_quota } = policyPayload(values, {
      workspaceRootAllowed,
    });
    const body: Record<string, unknown> = {
      display_name: values.display_name?.trim() || null,
      email: values.email?.trim() || null,
      token_quota,
      // ``/filesystem/defaults`` is admin-only, so a drawer without the
      // root-dir picker cannot show the value it would clear: keep it.
      ...(workspaceRootAllowed ? { workspace_root_dir } : {}),
    };
    if (admin) {
      body.role = values.role;
      body.org_unit = values.role === "admin" ? null : values.org_unit ?? null;
      body.permissions =
        values.role === "admin" ? [] : values.permissions ?? [];
      // Deny is admin-only on the backend, and a PATCH carries it as
      // omit = keep / null = clear / array = set. Only a touched picker sends
      // it, so saving the display name never rewrites the stored deny list.
      if (denyTouched) {
        const denied = values.denied_permissions ?? [];
        body.denied_permissions = denied.length > 0 ? denied : null;
      }
    } else if (canSubmitPermissions) {
      body.permissions = values.permissions ?? [];
    }
    try {
      await request(`/users/${editTarget.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setEditTarget(null);
      editForm.resetFields();
      void refreshUsers();
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : t("adminUsers.updateFailed"),
      );
    } finally {
      setEditSubmitting(false);
    }
  };

  const onDelete = async (row: UserRow) => {
    try {
      await request(`/users/${row.id}`, { method: "DELETE" });
      message.success(t("adminUsers.deleteSuccess"));
      void refreshAll();
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : t("common.deleteFailed"),
      );
    }
  };

  const onResetSubmit = async (values: ResetValues) => {
    if (!resetTarget) return;
    setResetSubmitting(true);
    try {
      await request(`/users/${resetTarget.id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ new_password: values.password }),
      });
      message.success(t("adminUsers.resetSuccess"));
      setResetTarget(null);
      resetForm.resetFields();
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : t("adminUsers.resetFailed"),
      );
    } finally {
      setResetSubmitting(false);
    }
  };

  const onUnlockLogin = async (row: UserRow) => {
    try {
      await request(`/users/${row.id}/unlock-login`, { method: "POST" });
      message.success(t("adminUsers.unlockLoginSuccess"));
      void refreshUsers();
    } catch (err) {
      message.error(
        err instanceof Error ? err.message : t("adminUsers.unlockLoginFailed"),
      );
    }
  };

  return (
    <>
      <div className={styles.pageTop}>
        <RoleLegend />
        <div className={expertStyles.gridToolbar}>
          <Input
            allowClear
            prefix={<Search size={14} />}
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder={t("adminUsers.searchPlaceholder")}
            className={styles.userSearch}
          />
          <div className={expertStyles.gridToolbarRight}>
            <Segmented
              size="small"
              value={viewMode}
              onChange={(v) => setViewMode(v as "table" | "card")}
              options={[
                {
                  value: "card",
                  label: (
                    <span className={expertStyles.viewModeLabel}>
                      <LayoutGrid size={14} />
                      {t("adminUsers.viewCard", "卡片")}
                    </span>
                  ),
                },
                {
                  value: "table",
                  label: (
                    <span className={expertStyles.viewModeLabel}>
                      <List size={14} />
                      {t("adminUsers.viewTable", "表格")}
                    </span>
                  ),
                },
              ]}
            />
            <Button
              icon={<RefreshCw size={14} />}
              onClick={() => void refreshAll()}
            >
              {t("common.refresh")}
            </Button>
            <Button
              icon={<Link2 size={14} />}
              onClick={() => setInviteOpen(true)}
            >
              {t("adminUsers.inviteUsers")}
            </Button>
            <Button
              type="primary"
              icon={<Plus size={14} />}
              onClick={openCreate}
            >
              {t("adminUsers.newUser")}
            </Button>
          </div>
        </div>
      </div>

      {showCardView ? (
        <UserCardGrid
          rows={filteredRows}
          loading={loading}
          agentsByUserId={agentsByUserId}
          agentsLoading={agentsLoading}
          currentUserId={currentUserId}
          permLabelByKey={permLabelByKey}
          orgUnitLabelByKey={orgUnitLabelByKey}
          onTogglePatch={togglePatch}
          onEdit={openEdit}
          onShowAgents={setAgentDrawerUser}
          onResetPassword={(row) => {
            setResetTarget(row);
            resetForm.resetFields();
          }}
          onDelete={onDelete}
          onUnlockLogin={onUnlockLogin}
          admin={admin}
          nowSec={nowSec}
        />
      ) : (
        <ResizableTable
          storageKey="admin-users"
          rowKey="id"
          size="middle"
          className={styles.userTable}
          loading={loading}
          dataSource={filteredRows}
          pagination={false}
          scroll={{ x: 1360 }}
          rowClassName={(row) =>
            [
              row.disabled ? styles.userTableRowDisabled : "",
              row.login_locked ? styles.userTableRowLocked : "",
            ]
              .filter(Boolean)
              .join(" ")
          }
          columns={[
            {
              title: t("adminUsers.colUsername"),
              width: 240,
              fixed: isMobile ? undefined : "left",
              render: (_, row) => {
                const displayName = row.display_name?.trim() || row.username;
                return (
                  <div className={styles.userCell}>
                    <span
                      className={`${styles.userCellAvatar} ${roleToneClass(
                        row.role,
                      )}`}
                    >
                      {userInitials(displayName, row.username)}
                    </span>
                    <span className={styles.userCellText}>
                      <span className={styles.userCellName}>
                        {displayName}
                        {row.id === currentUserId && (
                          <span className={styles.userCellYou}>
                            {t("adminUsers.you")}
                          </span>
                        )}
                      </span>
                      <span className={styles.userCellHandle}>
                        @{row.username}
                      </span>
                    </span>
                  </div>
                );
              },
            },
            {
              title: t("adminUsers.colEmail"),
              width: 180,
              ellipsis: true,
              render: (_, row) => (
                <span className={styles.userCellMuted}>
                  {row.email?.trim() || "—"}
                </span>
              ),
            },
            {
              title: t("adminUsers.colAuth"),
              width: 120,
              render: (_, row) => {
                const parts = [
                  row.sso_linked ? t("adminUsers.ssoBadge") : null,
                  row.has_password ? t("adminUsers.passwordBadge") : null,
                ].filter(Boolean);
                return (
                  <span className={styles.userCellMuted}>
                    {parts.length ? parts.join(" · ") : "—"}
                  </span>
                );
              },
            },
            {
              title: t("adminUsers.colAgents"),
              width: 80,
              render: (_, row) => {
                const count = agentsByUserId.get(row.id)?.length ?? 0;
                return (
                  <button
                    type="button"
                    className={styles.userCellLink}
                    onClick={() => setAgentDrawerUser(row)}
                  >
                    <Bot size={13} />
                    {agentsLoading ? "…" : count}
                  </button>
                );
              },
            },
            {
              title: t("adminUsers.colRole"),
              width: 112,
              render: (_, row) => {
                const unitLabel = row.org_unit
                  ? orgUnitLabelByKey.get(row.org_unit)
                  : undefined;
                return (
                  <div className={styles.userRoleCell}>
                    <span
                      className={`${styles.userCardPill} ${roleToneClass(
                        row.role,
                      )}`}
                    >
                      {t(ROLE_LABEL_KEYS[row.role])}
                    </span>
                    {unitLabel && (
                      <span className={styles.userRoleUnit}>{unitLabel}</span>
                    )}
                  </div>
                );
              },
            },
            {
              title: t("adminUsers.colPermissions"),
              width: 120,
              render: (_, row) => (
                <PermissionSummary row={row} permLabelByKey={permLabelByKey} />
              ),
            },
            {
              title: t("common.enabled"),
              width: 72,
              // Admin-only toggle: everyone else reads the state, no control.
              render: (_, row) =>
                admin ? (
                  <Switch
                    size="small"
                    checked={!row.disabled}
                    onChange={(checked) =>
                      togglePatch(row, { disabled: !checked })
                    }
                  />
                ) : (
                  <span className={styles.userCellMuted}>
                    {row.disabled
                      ? t("adminUsers.statusDisabled")
                      : t("adminUsers.statusEnabled")}
                  </span>
                ),
            },
            {
              title: t("adminUsers.colCreatedAt"),
              dataIndex: "created_at",
              width: 156,
              render: (ts: number | undefined) => (
                <span className={styles.userCellMuted}>
                  {formatUserTs(ts, timeZone)}
                </span>
              ),
            },
            {
              title: t("adminUsers.colLoginLock"),
              width: 180,
              render: (_, row) => (
                <UserLoginLock
                  row={row}
                  nowSec={nowSec}
                  onUnlock={() => void onUnlockLogin(row)}
                />
              ),
            },
            {
              title: t("adminUsers.colActions"),
              width: 120,
              fixed: isMobile ? undefined : "right",
              render: (_, row) => (
                <Space size={4}>
                  <Tooltip title={t("common.edit")}>
                    <button
                      type="button"
                      className={styles.userCardIconBtn}
                      onClick={() => openEdit(row)}
                      aria-label={t("common.edit")}
                    >
                      <Pencil size={14} />
                    </button>
                  </Tooltip>
                  {admin && (
                    <Tooltip title={t("adminUsers.resetPassword")}>
                      <button
                        type="button"
                        className={styles.userCardIconBtn}
                        onClick={() => {
                          setResetTarget(row);
                          resetForm.resetFields();
                        }}
                        aria-label={t("adminUsers.resetPassword")}
                      >
                        <KeyRound size={14} />
                      </button>
                    </Tooltip>
                  )}
                  {admin && (
                    <Popconfirm
                      title={t("adminUsers.deleteConfirm", {
                        username: row.username,
                      })}
                      onConfirm={() => onDelete(row)}
                      disabled={row.id === currentUserId}
                    >
                      <Tooltip
                        title={
                          row.id === currentUserId
                            ? t("adminUsers.deleteSelf")
                            : t("common.delete")
                        }
                      >
                        <button
                          type="button"
                          className={`${styles.userCardIconBtn} ${styles.userCardIconBtnDanger}`}
                          disabled={row.id === currentUserId}
                          aria-label={t("common.delete")}
                        >
                          <Trash2 size={14} />
                        </button>
                      </Tooltip>
                    </Popconfirm>
                  )}
                </Space>
              ),
            },
          ]}
        />
      )}

      <Drawer
        title={
          agentDrawerUser
            ? t("adminUsers.agentsDrawerTitle", {
                username: agentDrawerUser.username,
              })
            : ""
        }
        open={agentDrawerUser !== null}
        onClose={() => setAgentDrawerUser(null)}
        width={400}
        destroyOnHidden
      >
        <Spin spinning={agentsLoading}>
          {drawerAgents.length === 0 ? (
            <Empty description={t("adminUsers.noAgents")} />
          ) : (
            <div
              className={expertStyles.cardGrid}
              style={{
                gridTemplateColumns: "1fr",
                padding: "8px 0 24px",
              }}
            >
              {drawerAgents.map((agent) => (
                <AgentCard
                  key={agent.agent_id}
                  agent={agent}
                  iconName={agent.icon_name}
                  iconUrl={agent.icon_url}
                  accentColor={agent.color}
                  // This drawer is every agent the user holds, so it holds features
                  // too — and the row's kind is what names it: a feature's agent is
                  // not an expert, so its id row does not say "Expert ID".
                  idLabelKey={
                    isFeatureAgent(agent)
                      ? "features.agentId"
                      : "experts.agentId"
                  }
                  onEdit={(id) =>
                    setEditAgent(
                      drawerAgents.find((a) => a.agent_id === id) ?? null,
                    )
                  }
                  onDeleted={handleDrawerDeleted}
                  onStateChange={handleDrawerStateChange}
                  onPollSettled={() => void refreshAgents()}
                />
              ))}
            </div>
          )}
        </Spin>
      </Drawer>

      <EditAgentDrawer
        open={editAgent !== null}
        agent={editAgent}
        onClose={() => setEditAgent(null)}
        onSaved={handleEditSaved}
      />

      <InviteDrawer open={inviteOpen} onClose={() => setInviteOpen(false)} />

      <Drawer
        title={t("adminUsers.modalNewTitle")}
        placement="right"
        open={createOpen}
        onClose={() => {
          setCreateOpen(false);
          form.resetFields();
        }}
        width={Math.min(
          520,
          typeof window !== "undefined" ? window.innerWidth - 24 : 520,
        )}
        destroyOnHidden
        className={styles.createUserDrawer}
        styles={{ body: { paddingTop: 12, paddingBottom: 24 } }}
        footer={
          <div className={styles.createUserFooter}>
            <Button
              onClick={() => {
                setCreateOpen(false);
                form.resetFields();
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="primary"
              loading={submitting}
              onClick={() => form.submit()}
            >
              {t("common.create")}
            </Button>
          </div>
        }
      >
        <Form<CreateValues>
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={onCreate}
          initialValues={{
            role: "user",
            permissions: [],
            limit_workspace_root: false,
            limit_token_quota: false,
          }}
          className={styles.createUserForm}
        >
          <div className={styles.createSection}>
            <div className={styles.createSectionTitle}>
              {t("adminUsers.createSectionAccount")}
            </div>
            <Form.Item
              label={t("adminUsers.formUsername")}
              name="username"
              rules={[
                { required: true, message: t("adminUsers.formUsername") },
                {
                  pattern: /^[a-zA-Z0-9_-]{1,64}$/,
                  message: t("wizard.admin.usernameRule"),
                },
              ]}
            >
              <Input prefix={<User {...FIELD_ICON_PROPS} />} autoFocus />
            </Form.Item>
            <Form.Item
              label={t("adminUsers.formDisplayName")}
              name="display_name"
            >
              <Input prefix={<IdCard {...FIELD_ICON_PROPS} />} />
            </Form.Item>
            <Form.Item
              label={t("adminUsers.formEmail")}
              name="email"
              rules={[
                {
                  type: "email",
                  message: t("adminUsers.formEmailInvalid"),
                },
              ]}
            >
              <Input
                prefix={<Mail {...FIELD_ICON_PROPS} />}
                type="email"
                autoComplete="email"
              />
            </Form.Item>
            <Form.Item
              label={t("adminUsers.formPassword")}
              name="password"
              rules={[
                { required: true, message: t("adminUsers.formPassword") },
              ]}
            >
              <Input.Password
                prefix={<Lock {...FIELD_ICON_PROPS} />}
                autoComplete="new-password"
              />
            </Form.Item>
            <Form.Item
              label={t("adminUsers.formPasswordConfirm")}
              name="confirm"
              dependencies={["password"]}
              rules={[
                {
                  required: true,
                  message: t("adminUsers.formPasswordConfirm"),
                },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue("password") === value) {
                      return Promise.resolve();
                    }
                    return Promise.reject(
                      new Error(t("wizard.admin.passwordMismatch")),
                    );
                  },
                }),
              ]}
            >
              <Input.Password
                prefix={<LockOpen {...FIELD_ICON_PROPS} />}
                autoComplete="new-password"
              />
            </Form.Item>
          </div>

          <div className={styles.createSection}>
            <div className={styles.createSectionTitle}>
              {t("adminUsers.createSectionAccess")}
            </div>
            <div
              className={`${styles.permAdminHint} ${styles.permSectionHint}`}
            >
              <CircleHelp size={15} strokeWidth={2} />
              <span>{t("adminUsers.permEditHint")}</span>
            </div>
            {/* ``_assert_admin(actor, "create a non-user account")``: every role
                but ``user`` is refused for a ``users`` holder, so the picker is
                admin-only. The form still carries the ``user`` default
                (``openCreate``), which keeps the plain path working. */}
            {admin && (
              <Form.Item
                label={t("adminUsers.formRole")}
                name="role"
                rules={[{ required: true }]}
                className={styles.createUserRoleItem}
              >
                <RolePicker options={createRoleOptions} />
              </Form.Item>
            )}
            <Form.Item
              noStyle
              shouldUpdate={(prev, cur) => prev.role !== cur.role}
            >
              {({ getFieldValue }) => {
                const isAdminRole = getFieldValue("role") === "admin";
                if (isAdminRole) {
                  return (
                    <div className={styles.permAdminHint}>
                      <ShieldCheck size={15} strokeWidth={2} />
                      <span>{t("adminUsers.permAllHint")}</span>
                    </div>
                  );
                }
                return (
                  <>
                    {/* Binding a department is a permission grant: admin-only
                        on create too (``_assert_admin``). */}
                    {admin && <OrgUnitField options={orgUnitOptions} />}
                    <Form.Item
                      label={t("adminUsers.colPermissions")}
                      name="permissions"
                      className={styles.createUserPermItem}
                    >
                      <PermissionCheckboxPicker catalog={assignableCatalog} />
                    </Form.Item>
                  </>
                );
              }}
            </Form.Item>
          </div>

          <ResourcePolicyFields
            fsTreeRoot={fsTreeRoot}
            workspaceRootAllowed={workspaceRootAllowed}
          />
        </Form>
      </Drawer>

      <Drawer
        title={
          editTarget
            ? t("adminUsers.modalEditTitle", {
                username: editTarget.username,
              })
            : t("common.edit")
        }
        placement="right"
        open={editTarget !== null}
        onClose={() => {
          setEditTarget(null);
          editForm.resetFields();
        }}
        width={Math.min(
          520,
          typeof window !== "undefined" ? window.innerWidth - 24 : 520,
        )}
        destroyOnHidden
        className={styles.createUserDrawer}
        styles={{ body: { paddingTop: 12, paddingBottom: 24 } }}
        footer={
          <div className={styles.createUserFooter}>
            <Button
              onClick={() => {
                setEditTarget(null);
                editForm.resetFields();
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="primary"
              loading={editSubmitting}
              onClick={() => editForm.submit()}
            >
              {t("common.save")}
            </Button>
          </div>
        }
      >
        <Form<EditValues>
          form={editForm}
          layout="vertical"
          requiredMark={false}
          onFinish={onEditSubmit}
          className={styles.createUserForm}
          onValuesChange={(changed) => {
            // Fires on user edits only (``setFieldsValue`` does not), which is
            // exactly the "touched" signal the three-state deny field needs.
            if ("denied_permissions" in changed) setDenyTouched(true);
            // Rebinding the department changes which keys its grants cover.
            if ("org_unit" in changed) loadUnitGrants(changed.org_unit);
          }}
        >
          <div className={styles.createSection}>
            <div className={styles.createSectionTitle}>
              {t("adminUsers.createSectionAccount")}
            </div>
            <Form.Item
              label={t("adminUsers.formDisplayName")}
              name="display_name"
            >
              <Input prefix={<IdCard {...FIELD_ICON_PROPS} />} />
            </Form.Item>
            <Form.Item
              label={t("adminUsers.formEmail")}
              name="email"
              rules={[
                {
                  type: "email",
                  message: t("adminUsers.formEmailInvalid"),
                },
              ]}
            >
              <Input
                prefix={<Mail {...FIELD_ICON_PROPS} />}
                type="email"
                autoComplete="email"
              />
            </Form.Item>
          </div>

          <div className={styles.createSection}>
            <div className={styles.createSectionTitle}>
              {t("adminUsers.createSectionAccess")}
            </div>
            <div
              className={`${styles.permAdminHint} ${styles.permSectionHint}`}
            >
              <CircleHelp size={15} strokeWidth={2} />
              <span>{t("adminUsers.permEditHint")}</span>
            </div>
            {/* ``_assert_admin``: role and department are admin-only writes. */}
            {admin && (
              <Form.Item
                label={t("adminUsers.formRole")}
                name="role"
                rules={[{ required: true }]}
                className={styles.createUserRoleItem}
                extra={
                  editTarget && isSelfAdmin(editTarget)
                    ? t("adminUsers.demoteSelf")
                    : undefined
                }
              >
                <RolePicker
                  options={createRoleOptions}
                  disabled={Boolean(editTarget && isSelfAdmin(editTarget))}
                />
              </Form.Item>
            )}
            <Form.Item
              noStyle
              shouldUpdate={(prev, cur) => prev.role !== cur.role}
            >
              {({ getFieldValue }) => {
                const isAdminRole = getFieldValue("role") === "admin";
                if (isAdminRole) {
                  return (
                    <div className={styles.permAdminHint}>
                      <ShieldCheck size={15} strokeWidth={2} />
                      <span>{t("adminUsers.permAllHint")}</span>
                    </div>
                  );
                }
                if (!admin && !canSubmitPermissions) {
                  // Target holds module keys this actor may not grant; the
                  // picker would only produce a 403 on save.
                  return null;
                }
                return (
                  <>
                    {admin && <OrgUnitField options={orgUnitOptions} />}
                    <Form.Item
                      noStyle
                      shouldUpdate={(prev, cur) =>
                        prev.permissions !== cur.permissions ||
                        prev.denied_permissions !== cur.denied_permissions
                      }
                    >
                      {({ getFieldValue }) => {
                        const granted = new Set<string>(
                          getFieldValue("permissions") ?? [],
                        );
                        const denied: string[] =
                          getFieldValue("denied_permissions") ?? [];
                        const deniedSet = new Set(denied);
                        // A deny outranks role, department and grant, so these
                        // keys are checked and still off: state it instead of
                        // leaving a tick that buys nothing on screen.
                        const beaten = denied.filter(
                          (key) => granted.has(key) || unitGrantedKeys.has(key),
                        );
                        return (
                          <>
                            {beaten.length > 0 ? (
                              <div className={styles.permDenyWarn}>
                                <TriangleAlert size={15} strokeWidth={2} />
                                <span>
                                  {t("adminUsers.permDenyConflict", {
                                    keys: beaten
                                      .map(
                                        (key) =>
                                          permShortLabelByKey.get(key) ?? key,
                                      )
                                      .join("、"),
                                  })}
                                </span>
                              </div>
                            ) : null}
                            <Form.Item
                              label={t("adminUsers.colPermissions")}
                              name="permissions"
                              className={styles.createUserPermItem}
                            >
                              <PermissionCheckboxPicker
                                catalog={assignableCatalog}
                                unitGrantedKeys={unitGrantedKeys}
                                deniedKeys={deniedSet}
                              />
                            </Form.Item>
                            {/* Deny is admin-only (``_assert_admin`` on the
                                users router), so it is both hidden without the
                                role and free of the key-level gate that limits
                                a non-admin's grants: full catalog. */}
                            {admin ? (
                              <Form.Item
                                label={t("adminUsers.permDenyLabel")}
                                name="denied_permissions"
                                extra={t("adminUsers.permDenyHint")}
                              >
                                <PermissionDenyPicker
                                  catalog={permCatalog}
                                  grantedKeys={granted}
                                  unitGrantedKeys={unitGrantedKeys}
                                />
                              </Form.Item>
                            ) : null}
                          </>
                        );
                      }}
                    </Form.Item>
                  </>
                );
              }}
            </Form.Item>
          </div>

          <ResourcePolicyFields
            fsTreeRoot={fsTreeRoot}
            workspaceRootAllowed={workspaceRootAllowed}
          />
        </Form>
      </Drawer>

      <Modal
        title={
          resetTarget
            ? t("adminUsers.modalResetTitle", {
                username: resetTarget.username,
              })
            : ""
        }
        open={resetTarget !== null}
        onCancel={() => {
          setResetTarget(null);
          resetForm.resetFields();
        }}
        onOk={() => resetForm.submit()}
        okText={t("common.reset")}
        cancelText={t("common.cancel")}
        confirmLoading={resetSubmitting}
      >
        <Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
          {t("adminUsers.resetHint")}
        </Text>
        <Form<ResetValues>
          form={resetForm}
          layout="vertical"
          onFinish={onResetSubmit}
        >
          <Form.Item
            label={t("adminUsers.newPassword")}
            name="password"
            rules={[
              { required: true, message: t("adminUsers.newPasswordRequired") },
            ]}
          >
            <Input.Password
              autoComplete="new-password"
              prefix={
                <Lock size={14} style={{ color: "var(--fn-text-tertiary)" }} />
              }
            />
          </Form.Item>
          <Form.Item
            label={t("adminUsers.formPasswordConfirm")}
            name="confirm"
            dependencies={["password"]}
            rules={[
              { required: true, message: t("adminUsers.formPasswordConfirm") },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue("password") === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(
                    new Error(t("wizard.admin.passwordMismatch")),
                  );
                },
              }),
            ]}
          >
            <Input.Password
              autoComplete="new-password"
              prefix={
                <LockOpen
                  size={14}
                  style={{ color: "var(--fn-text-tertiary)" }}
                />
              }
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
