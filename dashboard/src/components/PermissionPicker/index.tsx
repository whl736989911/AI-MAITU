/**
 * The module-permission picker — one implementation for every page that edits
 * a set of permission keys (design §5.3: the user authorization page and a
 * department's grants are the same catalog, grouped the same way).
 *
 * The catalog comes from ``GET /api/users/permissions``, which answers with
 * ``can_grant`` per key: the server resolves the actor's ``role ∪ department ∪
 * grant − deny`` (with the admin bypass) through the same function the write
 * paths check, so this component never re-derives who may hand out what. A
 * caller that may not grant a key simply does not put it in ``catalog``.
 */

import { useMemo } from "react";
import { Checkbox, Typography } from "antd";
import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import { request } from "../../api/request";
import styles from "./index.module.less";

const { Text } = Typography;

export interface PermissionCatalogItem {
  key: string;
  category: string;
  label: string;
  page?: string;
  page_label?: string;
  /**
   * Whether the signed-in operator may hand this key out — answered by the
   * server (design §4.1), never re-derived here: it resolves the actor's
   * ``role ∪ department ∪ grant − deny`` with the admin bypass through
   * ``effective_permissions``, the same function the write path checks
   * (``assert_can_grant``). Re-deriving it from the signed-in user's own module
   * list got the department leg and the admin bypass wrong in both directions.
   */
  can_grant: boolean;
  /** Sent by the catalog: key derived rather than written out (channel types). */
  dynamic?: boolean;
}

/** The catalog as the server publishes it, for the page that edits a set. */
export function fetchPermissionCatalog(): Promise<PermissionCatalogItem[]> {
  return request<PermissionCatalogItem[]>("/users/permissions");
}

/** One category of the permission catalog, split into page groups. */
export interface PermissionGroup {
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
export function groupPermissionCatalog(
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
export type ChipTag = "own" | "unit" | "deny";

/**
 * Provenance markers shown on a chip. Both pickers pass only the sources that
 * add information there: the deny picker marks what a deny would take away
 * (granted directly / by the department), the grant picker marks the two
 * reasons a tick or an untick would not decide the outcome (a department
 * grant, and the deny list, which outranks everything).
 */
export function chipSources(
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
  own: "perms.sourceOwn",
  unit: "perms.sourceUnit",
  deny: "perms.denyTag",
};

export function ChipTagList({ tags }: { tags: ChipTag[] }) {
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

export interface PermissionCheckboxPickerProps {
  value?: string[];
  onChange?: (value: string[]) => void;
  catalog: PermissionCatalogItem[];
  disabled?: boolean;
  /** Keys the account's org unit grants: a cleared box does not remove them. */
  unitGrantedKeys?: Set<string>;
  /** Keys on the deny list: a ticked box does not turn them on. */
  deniedKeys?: Set<string>;
}

export function PermissionCheckboxPicker({
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
        settings: t("perms.groupSettings"),
        control: t("perms.groupControl"),
        admin: t("perms.groupAdmin"),
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
        <Text type="secondary">{t("perms.catalogEmpty")}</Text>
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
                ? t("perms.grantDeniedNote")
                : tags.includes("unit")
                ? t("perms.grantUnitNote")
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
