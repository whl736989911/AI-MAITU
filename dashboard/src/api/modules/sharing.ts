import { request } from "../request";

/** Resource kinds the backend shares through one ACL table. */
export type SharingResourceType =
  | "agent"
  | "connector"
  | "knowledge_base"
  | "feature";

/** Who may reach the resource by default. */
export type SharingVisibility = "private" | "unit" | "public";

/**
 * What reaching the resource lets someone *do*: ``read`` reaches it (list it,
 * open it, search it), ``write`` also maintains it. ``read`` is the default,
 * so a share nobody chose a level for — the published enterprise space
 * included — is reachable but not writable.
 */
export type SharingPermission = "read" | "write";

/** How widely a change lands: the actor alone, their unit, or the whole org. */
export type SharingImpactScope = "self" | "unit" | "org";

/**
 * Lifecycle of one logged change. Applied/rejected/rolled-back rows are kept
 * forever: the change log is the audit trail, not a queue that empties.
 */
export type SharingChangeStatus =
  | "applied"
  | "pending_approval"
  | "rejected"
  | "rolled_back";

/** Kinds of extra grantee an entry may carry; grants only ever widen access. */
export type SharingGranteeType = "user" | "unit" | "role";

export interface SharingGrant {
  grantee_type: SharingGranteeType;
  grantee_id: string;
}

/** One resource's access state. */
export interface SharingAclEntry {
  resource_type: string;
  resource_id: string;
  /** ``null`` for system-owned rows (template agents with no owner). */
  owner_user_id: number | null;
  visibility: SharingVisibility;
  /** Org unit snapshotted at share time; only meaningful for ``unit``. */
  unit_key: string | null;
  permission: SharingPermission;
  version: number;
  grants: SharingGrant[];
}

export interface SharingAclResponse {
  resource_type: string;
  resource_id: string;
  /** ``null`` when nobody has shared the resource yet. */
  entry: SharingAclEntry | null;
}

/** Body of ``POST /sharing/acl/{resource_type}/{resource_id}``. */
export interface SharingAclChangeBody {
  visibility: SharingVisibility;
  /** Org unit snapshot for ``unit``; omit to snapshot the caller's own unit. */
  unit_key?: string | null;
  /** ``read`` (default) reaches the resource, ``write`` also maintains it. */
  permission?: SharingPermission;
  grants?: SharingGrant[];
  reason?: string | null;
}

/**
 * Outcome of one access change.
 *
 * ``status`` / ``applied`` — never the HTTP code — say whether the change is
 * live: a change that would reach the whole organization is *accepted* and
 * recorded as ``pending_approval``, and the request still answers 200.
 */
export interface SharingChangeResult {
  change_id: string;
  resource_type: string;
  resource_id: string;
  status: SharingChangeStatus;
  impact_scope: SharingImpactScope;
  /** True only while this change's state is the one in force. */
  applied: boolean;
  entry: SharingAclEntry | null;
}

/** One row of the change log, with both sides decoded for review. */
export interface SharingChange {
  change_id: string;
  resource_type: SharingResourceType;
  resource_id: string;
  /** Name resolved by the backend; ``null`` when deleted or unnamed. */
  resource: { name: string | null };
  actor_user_id: number;
  status: SharingChangeStatus;
  impact_scope: SharingImpactScope;
  applied: boolean;
  from_version: number;
  to_version: number;
  reason: string | null;
  /** Epoch seconds. */
  created_at: number;
  before: SharingAclEntry;
  after: SharingAclEntry;
}

export interface SharingChangeListResponse {
  status: SharingChangeStatus;
  limit: number;
  changes: SharingChange[];
}

/** Org unit catalog row (``GET /api/org-units``) — the ``unit`` target list. */
export interface SharingOrgUnit {
  key: string;
  label: { zh?: string; en?: string };
  parent_key: string | null;
}

/** Every status the queue can filter on, newest-first within each window. */
export const SHARING_CHANGE_STATUSES: readonly SharingChangeStatus[] = [
  "pending_approval",
  "applied",
  "rejected",
  "rolled_back",
];

/** The queue is a reviewer's screen: the backend caps the window at 200. */
export const DEFAULT_SHARING_QUEUE_LIMIT = 50;

export const sharingApi = {
  /** The ACL in force, or ``entry: null`` when nobody has shared it yet. */
  getAcl: (resourceType: SharingResourceType, resourceId: string) =>
    request<SharingAclResponse>(
      `/sharing/acl/${resourceType}/${encodeURIComponent(resourceId)}`,
    ),

  /** Apply an access change — or park it for approval when it reaches the org. */
  changeAcl: (
    resourceType: SharingResourceType,
    resourceId: string,
    body: SharingAclChangeBody,
  ) =>
    request<SharingChangeResult>(
      `/sharing/acl/${resourceType}/${encodeURIComponent(resourceId)}`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  listChanges: (
    status: SharingChangeStatus = "pending_approval",
    limit: number = DEFAULT_SHARING_QUEUE_LIMIT,
  ) =>
    request<SharingChangeListResponse>(
      `/sharing/changes?status=${status}&limit=${limit}`,
    ),

  /** Admin-only. 409 when the change is no longer pending. */
  approveChange: (changeId: string) =>
    request<SharingChangeResult>(
      `/sharing/changes/${encodeURIComponent(changeId)}/approve`,
      { method: "POST" },
    ),

  /** Admin-only. Rejection is final and carries a reason for the requester. */
  rejectChange: (changeId: string, reason: string) =>
    request<SharingChangeResult>(
      `/sharing/changes/${encodeURIComponent(changeId)}/reject`,
      { method: "POST", body: JSON.stringify({ reason }) },
    ),

  /** Restore what a change replaced; logged as a new change. 409 when not applied. */
  rollbackChange: (changeId: string) =>
    request<SharingChangeResult>(
      `/sharing/changes/${encodeURIComponent(changeId)}/rollback`,
      { method: "POST" },
    ),

  /**
   * Org units for the ``unit`` visibility picker. Failure just leaves the
   * selector empty, so a missing unit catalog never blocks the drawer.
   */
  listOrgUnits: () =>
    request<{ units: SharingOrgUnit[] }>("/org-units").then(
      (res) => res.units ?? [],
    ),
};
