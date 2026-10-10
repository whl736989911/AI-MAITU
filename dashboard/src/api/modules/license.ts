import { request } from "../request";

export type LicenseState =
  | "UNACTIVATED"
  | "ACTIVE"
  | "CACHED_ACTIVE"
  | "LOCKED";
export type LicenseReason =
  | "unactivated"
  | "expired"
  | "revoked"
  | "customer_disabled"
  | "cache_expired"
  | "invalid_credential"
  | "clock_anomaly"
  | "configuration_required"
  | "network_error";

export interface LicenseStatus {
  state: LicenseState;
  allowed: boolean;
  reason: LicenseReason | null;
  license_expires_at: number | null;
  valid_until: number | null;
  contact: string;
  timezone: string;
}

export interface LicenseDetails extends LicenseStatus {
  last_sync_at: number | null;
  last_sync_error: string | null;
  instance_id: string;
  public_key_fingerprint: string;
  license_revision: number | null;
}

export const licenseApi = {
  status: () => request<LicenseStatus>("/license/status"),
  details: () => request<LicenseDetails>("/license/details"),
  activate: (activation_code: string) =>
    request<LicenseDetails>("/license/activate", {
      method: "POST",
      body: JSON.stringify({ activation_code }),
    }),
  refresh: () =>
    request<LicenseDetails>("/license/refresh", {
      method: "POST",
      body: JSON.stringify({}),
    }),
};
