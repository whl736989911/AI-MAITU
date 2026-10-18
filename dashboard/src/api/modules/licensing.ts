import { request } from "../request";

export type LicenseState =
  | "UNACTIVATED"
  | "ACTIVE"
  | "CACHED_ACTIVE"
  | "LOCKED";
export type LicenseReason =
  | "unactivated"
  | "revoked"
  | "expired"
  | "cache_expired"
  | "invalid_credential"
  | "time_anomaly"
  | "configuration"
  | null;

export interface LicenseSnapshot {
  managed: boolean;
  state: LicenseState | null;
  business_allowed: boolean;
  reason: LicenseReason;
  instance_id: string;
  license_id: string | null;
  license_version: number | null;
  license_expires_at: number | null;
  credential_type?: "online" | "offline" | null;
  expiring_soon: boolean;
  last_sync_at?: number | null;
  sync_error?: string | null;
}

export interface OfflineLicenseRequest {
  instance_id: string;
  public_key_pem: string;
  key_fingerprint: string;
  proof: string;
}

export interface LicenseReminder {
  show: boolean;
  expires_at: number | null;
  remaining_seconds: number | null;
  date: string | null;
}

export const licensingApi = {
  getStatus: () => request<LicenseSnapshot>("/license/status"),
  getDetails: () => request<LicenseSnapshot>("/license/details"),
  activate: (code: string) =>
    request<LicenseSnapshot>("/license/activate", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  refresh: () =>
    request<LicenseSnapshot>("/license/refresh", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  getOfflineRequest: () =>
    request<OfflineLicenseRequest>("/license/offline-request"),
  importOffline: (token: string) =>
    request<LicenseSnapshot>("/license/offline-import", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  claimReminder: () =>
    request<LicenseReminder>("/license/reminder", {
      method: "POST",
      body: JSON.stringify({}),
    }),
};
