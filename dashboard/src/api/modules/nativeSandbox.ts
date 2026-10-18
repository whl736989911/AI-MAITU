import { request } from "../request";

export type BubblewrapStatus = "ready" | "installed" | "skipped" | "degraded";
export type BubblewrapDiagnosis =
  | "missing_binary"
  | "install_permission_denied"
  | "userns_denied"
  | "runtime_failure"
  | null;

export interface BubblewrapStatusResult {
  status: BubblewrapStatus;
  reason: string | null;
  detail: string | null;
  diagnosis: BubblewrapDiagnosis;
  commands: string[];
}

export const nativeSandboxApi = {
  getBubblewrapStatus: () =>
    request<BubblewrapStatusResult>("/filesystem/bwrap-status"),
  ensureBubblewrap: () =>
    request<BubblewrapStatusResult>("/filesystem/ensure-bwrap", {
      method: "POST",
    }),
};
