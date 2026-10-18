export type Role = "owner" | "operator" | "reader";
export interface Session {
  user: { id: number; username: string; role: Role };
  csrf_token: string;
  timezone: string;
}
export interface Customer {
  id: string;
  name: string;
  contact: string;
  notes: string;
  active: boolean;
  version: number;
  created_at: number;
}
export interface Instance {
  id: string;
  customer_id: string;
  public_key_fingerprint: string;
  status: string;
  last_seen_at: number | null;
  license_id: string;
  license_expires_at: number;
  license_status: string;
  license_revision: number;
}
export interface Activation {
  id: string;
  customer_id: string;
  code?: string | null;
  expires_at: number;
  used_at: number | null;
  revoked_at: number | null;
}
export interface AuditEntry {
  id: number;
  actor: string;
  action: string;
  object_type: string;
  object_id: string;
  before_json: string;
  after_json: string;
  reason: string;
  created_at: number;
}
export interface Operator {
  id: number;
  username: string;
  role: Role;
  active: boolean;
  created_at: number;
}
export interface Enrollment {
  id: number;
  username: string;
  role: Role;
  totp_secret: string;
  provisioning_uri: string;
  active: boolean;
}
export interface Overview {
  customers: number;
  active_customers: number;
  instances: number;
  active_licenses: number;
  expired_licenses: number;
  revoked_licenses: number;
  expiring_soon: number;
  recent_contacts: { id: string; last_seen_at: number }[];
  timezone: string;
}
export interface SystemReadout {
  service_status: string;
  timezone: string;
  tls_certificate_configured: boolean;
  tls_certificate_expires_at: number | null;
  tls_certificate_readable: boolean;
  active_signing_kid: string | null;
  staged_signing_kids: string[];
}
export interface ActivationIssue {
  id: string;
  customer_id: string;
  code: string;
  expires_at: number;
  used_at: number | null;
  revoked_at: number | null;
}
export interface OfflineLicenseIssue {
  token: string;
  license_id: string;
  license_version: number;
}
export interface Release {
  id: string;
  version: string;
  filename: string;
  sha256: string;
  size: number;
  release_notes: string;
  package_type: string;
  platform: string;
  architecture: string;
  published: boolean;
  created_at: string;
  published_at: string | null;
}
export interface ReleaseSigningKey {
  kid: string;
  public_pem: string;
  state: string;
  created_at: number;
}
const base = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
let csrfToken = "";
const json = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  if (method !== "GET" && method !== "HEAD" && path !== "/api/v1/admin/login") {
    if (!csrfToken)
      throw new Error(
        "The authenticated session has no CSRF token. Sign in again.",
      );
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(`${base}${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = data as {
      error?: { code?: string; message?: string };
    } | null;
    const error = new Error(
      envelope?.error?.message || `${response.status} ${response.statusText}`,
    );
    Object.assign(error, {
      status: response.status,
      code: envelope?.error?.code,
    });
    throw error;
  }
  return data as T;
}
export const api = {
  session: async () => {
    const s = await request<Session>("/api/v1/admin/session");
    csrfToken = s.csrf_token;
    return s;
  },
  login: async (username: string, password: string, totp: string) => {
    const s = await request<Session>(
      "/api/v1/admin/login",
      json("POST", { username, password, totp }),
    );
    csrfToken = s.csrf_token;
    return s;
  },
  logout: async () => {
    await request<void>("/api/v1/admin/logout", json("POST", {}));
    csrfToken = "";
  },
  reauth: async (totp: string) => {
    const s = await request<Session>(
      "/api/v1/admin/reauth",
      json("POST", { totp }),
    );
    csrfToken = s.csrf_token;
    return s;
  },
  customers: () => request<Customer[]>("/api/v1/admin/customers"),
  createCustomer: (input: { name: string; contact: string; notes: string }) =>
    request<Customer>("/api/v1/admin/customers", json("POST", input)),
  updateCustomer: (
    id: string,
    input: {
      name: string;
      contact: string;
      notes: string;
      active: boolean;
      version: number;
      reason?: string;
    },
  ) =>
    request<Customer>(
      `/api/v1/admin/customers/${encodeURIComponent(id)}`,
      json("PUT", input),
    ),
  instances: () => request<Instance[]>("/api/v1/admin/instances"),
  activations: () => request<Activation[]>("/api/v1/admin/activation-codes"),
  createActivation: (input: {
    customer_id: string;
    expires_at: number;
    ttl_seconds: number;
  }) =>
    request<ActivationIssue>(
      "/api/v1/admin/activation-codes",
      json("POST", input),
    ),
  revokeActivation: (id: string, reason: string) =>
    request<Activation>(
      `/api/v1/admin/activation-codes/${encodeURIComponent(id)}/revoke`,
      json("POST", { reason }),
    ),
  createOfflineLicense: (input: {
    customer_id: string;
    request: Record<string, string>;
    license_expires_at: number;
    offline_expires_at: number;
    reason: string;
  }) =>
    request<OfflineLicenseIssue>(
      "/api/v1/admin/offline-licenses",
      json("POST", input),
    ),
  releases: () => request<Release[]>("/api/v1/admin/releases"),
  uploadRelease: (filename: string, file: File) =>
    request<Release>(`/api/v1/admin/releases/${encodeURIComponent(filename)}`, {
      method: "POST",
      headers: { "Content-Type": "application/zip" },
      body: file,
    }),
  publishRelease: (id: string, reason: string) =>
    request<Release>(
      `/api/v1/admin/releases/${encodeURIComponent(id)}/publish`,
      json("POST", { reason }),
    ),
  releaseSigningKeys: () =>
    request<ReleaseSigningKey[]>("/api/v1/admin/release-signing-keys"),
  stageReleaseKey: (reason: string) =>
    request<{ kid: string; public_pem: string; state: string }>(
      "/api/v1/admin/release-signing-keys/stage",
      json("POST", { reason }),
    ),
  activateReleaseKey: (kid: string, reason: string) =>
    request<{ kid: string; state: string }>(
      `/api/v1/admin/release-signing-keys/${encodeURIComponent(kid)}/activate`,
      json("POST", { reason }),
    ),
  audit: (beforeId?: number) =>
    request<AuditEntry[]>(
      `/api/v1/admin/audit?limit=100${
        beforeId === undefined ? "" : `&before_id=${beforeId}`
      }`,
    ),
  extend: (
    id: string,
    input: { expires_at?: number; reason: string; version: number },
  ) =>
    request(
      `/api/v1/admin/licenses/${encodeURIComponent(id)}/extend`,
      json("POST", input),
    ),
  revoke: (id: string, input: { reason: string; version: number }) =>
    request(
      `/api/v1/admin/licenses/${encodeURIComponent(id)}/revoke`,
      json("POST", input),
    ),
  restore: (
    id: string,
    input: { expires_at?: number; reason: string; version: number },
  ) =>
    request(
      `/api/v1/admin/licenses/${encodeURIComponent(id)}/restore`,
      json("POST", input),
    ),
  replace: (
    id: string,
    input: { reason: string; expires_at?: number; version: number },
  ) =>
    request<ActivationIssue>(
      `/api/v1/admin/instances/${encodeURIComponent(id)}/replace`,
      json("POST", input),
    ),
  overview: () => request<Overview>("/api/v1/admin/overview"),
  operators: () => request<Operator[]>("/api/v1/admin/users"),
  createOperator: (input: {
    username: string;
    password: string;
    role: "operator" | "reader";
  }) => request<Enrollment>("/api/v1/admin/users", json("POST", input)),
  updateOperator: (
    id: number,
    input: { role: Role; active: boolean; totp?: string },
  ) => request<Operator>(`/api/v1/admin/users/${id}`, json("PUT", input)),
  confirmTotp: (id: number, totp: string) =>
    request<Operator>(
      `/api/v1/admin/users/${id}/enroll`,
      json("POST", { totp }),
    ),
  system: () => request<SystemReadout>("/api/v1/admin/system"),
};
