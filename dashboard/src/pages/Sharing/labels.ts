import type {
  SharingChangeStatus,
  SharingGranteeType,
  SharingImpactScope,
  SharingResourceType,
  SharingVisibility,
} from "../../api/modules/sharing";

/**
 * The vocabulary the sharing UI renders, mapped to i18n keys. Kept in one
 * place so the queue, the drawer, and the tags can never drift apart.
 */

/**
 * Resource-type picker order; knowledge bases first — they are the entry point.
 *
 * Only the types this page can list are here; a stored change of another type
 * still renders its label below.
 */
export const SHARING_RESOURCE_TYPE_ORDER: readonly SharingResourceType[] = [
  "knowledge_base",
  "agent",
  "connector",
];

/** Every type the ACL table holds — the picker's subset plus any it no longer offers. */
export const RESOURCE_TYPE_LABEL_KEYS: Record<SharingResourceType, string> = {
  agent: "sharing.resourceType.agent",
  connector: "sharing.resourceType.connector",
  knowledge_base: "sharing.resourceType.knowledge_base",
  feature: "sharing.resourceType.feature",
};

export const VISIBILITY_LABEL_KEYS: Record<SharingVisibility, string> = {
  private: "sharing.visibility.private",
  unit: "sharing.visibility.unit",
  public: "sharing.visibility.public",
};

/** Longer copy for the visibility radio cards. */
export const VISIBILITY_HINT_KEYS: Record<SharingVisibility, string> = {
  private: "sharing.visibilityHint.private",
  unit: "sharing.visibilityHint.unit",
  public: "sharing.visibilityHint.public",
};

export const IMPACT_LABEL_KEYS: Record<SharingImpactScope, string> = {
  self: "sharing.impact.self",
  unit: "sharing.impact.unit",
  org: "sharing.impact.org",
};

/** Short form for chips; the long form is the tooltip. */
export const IMPACT_SHORT_KEYS: Record<SharingImpactScope, string> = {
  self: "sharing.impactShort.self",
  unit: "sharing.impactShort.unit",
  org: "sharing.impactShort.org",
};

export const STATUS_LABEL_KEYS: Record<SharingChangeStatus, string> = {
  pending_approval: "sharing.status.pending_approval",
  applied: "sharing.status.applied",
  rejected: "sharing.status.rejected",
  rolled_back: "sharing.status.rolled_back",
};

/** What a status means for the row, in one line. */
export const STATUS_NOTE_KEYS: Record<SharingChangeStatus, string> = {
  pending_approval: "sharing.queue.notePending",
  applied: "sharing.queue.noteApplied",
  rejected: "sharing.queue.noteRejected",
  rolled_back: "sharing.queue.noteRolledBack",
};

export const GRANTEE_TYPE_LABEL_KEYS: Record<SharingGranteeType, string> = {
  user: "sharing.settings.granteeType.user",
  unit: "sharing.settings.granteeType.unit",
  role: "sharing.settings.granteeType.role",
};
