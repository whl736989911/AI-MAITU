import { request } from "../request";
import type {
  SharingChangeResult,
  SharingGrant,
  SharingPermission,
  SharingVisibility,
} from "./sharing";

export type KnowledgePermissionScope =
  | "company"
  | "department"
  | "personal"
  | "custom";

export interface KnowledgePermissionMetadata {
  resource_type: "knowledge_base" | "knowledge_document";
  resource_id: string;
  path: string;
  access_scope: KnowledgePermissionScope;
  department_name: string | null;
  inherited_from: {
    resource_type: "knowledge_base" | "knowledge_document";
    resource_id: string;
    path: string;
  } | null;
  /** Current actor's effective permission; `null` means no access. */
  permission: SharingPermission | null;
  effective: { read: boolean; write: boolean };
  can_manage: boolean;
  can_delegate: boolean;
  version: number;
  entry: {
    visibility: SharingVisibility;
    unit_key: string | null;
    permission: SharingPermission;
    grants: SharingGrant[];
    version: number;
  } | null;
  pending: {
    change_id: string;
    status: string;
    impact_scope: string;
    reason: string | null;
    created_at: number;
  } | null;
}

export interface KnowledgePermissionsOverview {
  knowledge_base: KnowledgePermissionMetadata;
  documents: KnowledgePermissionMetadata[];
}

export interface KnowledgePermissionChangeResponse {
  permission: KnowledgePermissionMetadata;
  change: Pick<SharingChangeResult, "change_id" | "status" | "impact_scope">;
}
export interface KnowledgePermissionPrincipals {
  users: { id: number; username: string; label: string }[];
  departments: { key: string; label: string }[];
  roles: { id: string; label: string }[];
}

export interface KnowledgePermissionChangeBody {
  expected_version: number;
  visibility: SharingVisibility;
  unit_key?: string | null;
  permission: SharingPermission;
  grants?: SharingGrant[];
  reason?: string | null;
}

export interface KnowledgeDelegationPrincipal {
  id: number;
  username: string;
  label: string;
}

export interface KnowledgeDelegationGrant {
  id: string;
  grantor_user_id: number;
  grantee_user_id: number;
  grantee_label: string;
  can_delegate: boolean;
  can_revoke: boolean;
}

export interface KnowledgeDelegations {
  version: number;
  can_delegate: boolean;
  permission: SharingPermission;
  grants: KnowledgeDelegationGrant[];
  principals: KnowledgeDelegationPrincipal[];
}

export interface KnowledgeDelegationBody {
  grantee_user_id: number;
  can_delegate?: boolean;
}

export const knowledgePermissionsApi = {
  principals: (baseId: string) =>
    request<KnowledgePermissionPrincipals>(
      `/knowledge-bases/${baseId}/permission-principals`,
    ),
  list: (baseId: string) =>
    request<KnowledgePermissionsOverview>(
      `/knowledge-bases/${baseId}/permissions`,
    ),
  getDocument: (baseId: string, documentId: string) =>
    request<KnowledgePermissionMetadata>(
      `/knowledge-bases/${baseId}/documents/${documentId}/permissions`,
    ),
  updateBase: (baseId: string, body: KnowledgePermissionChangeBody) =>
    request<KnowledgePermissionChangeResponse>(
      `/knowledge-bases/${baseId}/permissions`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  updateDocument: (
    baseId: string,
    documentId: string,
    body: KnowledgePermissionChangeBody,
  ) =>
    request<KnowledgePermissionChangeResponse>(
      `/knowledge-bases/${baseId}/documents/${documentId}/permissions`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  delegations: (baseId: string, documentId: string) =>
    request<KnowledgeDelegations>(
      `/knowledge-bases/${baseId}/documents/${documentId}/delegations`,
    ),
  addDelegation: (
    baseId: string,
    documentId: string,
    body: KnowledgeDelegationBody,
  ) =>
    request<KnowledgeDelegations>(
      `/knowledge-bases/${baseId}/documents/${documentId}/delegations`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  revokeDelegation: (
    baseId: string,
    documentId: string,
    delegationId: string,
  ) =>
    request<void>(
      `/knowledge-bases/${baseId}/documents/${documentId}/delegations/${encodeURIComponent(
        delegationId,
      )}`,
      { method: "DELETE" },
    ),
};
