import { request } from "../request";

/**
 * Org-unit directory (``/api/org-units``) — the hierarchy an account's resource
 * scope hangs off.
 *
 * Reads sit behind the ``users`` module and every write behind ``require_admin``.
 * A refused write still answers with the usual error envelope, and
 * ``details.reason`` / ``details.children`` / ``details.users`` carry the actual
 * cause — the reused error code names the wrong kind of object, so the page
 * prefers those detail fields over the code's localized text.
 */

/** Bilingual unit name — both columns are NOT NULL server-side. */
export interface OrgUnitLabel {
  zh: string;
  en: string;
}

/**
 * One row of ``GET /api/org-units``: the server returns a *flat* list ordered by
 * ``sort_order, key``, and the hierarchy is rebuilt from ``parent_key``.
 */
export interface OrgUnit {
  key: string;
  label: OrgUnitLabel;
  parent_key: string | null;
}

/** Create / update responses echo the stored order the editor round-trips. */
export interface OrgUnitDetail extends OrgUnit {
  sort_order: number;
}

export interface OrgUnitListResponse {
  units: OrgUnit[];
}

/** ``POST /api/org-units`` — ``sort_order`` defaults to 0 server-side. */
export interface OrgUnitCreateBody {
  key: string;
  label_zh: string;
  label_en: string;
  parent_key: string | null;
  sort_order?: number;
}

/** ``PATCH /api/org-units/{key}`` — an omitted field keeps its stored value. */
export interface OrgUnitUpdateBody {
  label_zh?: string;
  label_en?: string;
  parent_key?: string | null;
  sort_order?: number;
}

/** Account fields this page reads; ``GET /api/users`` carries the unit scope. */
interface UserScopeRow {
  username: string;
  org_unit?: string | null;
}

/** ``GET``/``PUT /api/org-units/{key}/permissions`` — the unit's own grants. */
export interface OrgUnitPermissions {
  unit_key: string;
  permissions: string[];
}

export const orgUnitsApi = {
  list: () => request<OrgUnitListResponse>("/org-units"),

  create: (body: OrgUnitCreateBody) =>
    request<OrgUnitDetail>("/org-units", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  update: (key: string, body: OrgUnitUpdateBody) =>
    request<OrgUnitDetail>(`/org-units/${encodeURIComponent(key)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  remove: (key: string) =>
    request<void>(`/org-units/${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),

  /**
   * The unit's own module grants — what ``setPermissions`` writes.
   *
   * Not the inherited union: the keys a member gains from an ancestor are that
   * ancestor's rows, and showing them here would let an edit below look like it
   * granted something it does not own.
   */
  getPermissions: (key: string) =>
    request<OrgUnitPermissions>(
      `/org-units/${encodeURIComponent(key)}/permissions`,
    ),

  /** Replace the unit's grants; the body is the whole set, so clearing revokes. */
  setPermissions: (key: string, permissions: string[]) =>
    request<OrgUnitPermissions>(
      `/org-units/${encodeURIComponent(key)}/permissions`,
      {
        method: "PUT",
        body: JSON.stringify({ permissions }),
      },
    ),

  /**
   * Usernames scoped to each unit, keyed by unit key.
   *
   * The directory payload carries no member count, so the accounts that block a
   * delete can only be found in the account list. That list is the *same*
   * ``users`` gate as the directory, so one credential already covers both calls.
   */
  listMembersByUnit: async (): Promise<Map<string, string[]>> => {
    const rows = await request<UserScopeRow[]>("/users");
    const byUnit = new Map<string, string[]>();
    for (const row of rows) {
      if (!row.org_unit) continue;
      const members = byUnit.get(row.org_unit);
      if (members) members.push(row.username);
      else byUnit.set(row.org_unit, [row.username]);
    }
    return byUnit;
  },
};
