import { request } from "../request";

export type UpgradePhase =
  | "idle"
  | "queued"
  | "downloading"
  | "validating"
  | "building"
  | "backing_up"
  | "stopping"
  | "installing"
  | "restarting"
  | "verifying"
  | "succeeded"
  | "failed";

export interface UpdateStatus {
  current_version: string;
  latest_version: string | null;
  has_update: boolean;
  is_editable: boolean;
  /** Non-null when the process was launched via `octop service start` (systemd or launchd). */
  service_mode: "systemd" | "launchd" | null;
  /** True when Octop is spawned by the Wails desktop shell (or ``OCTOP_DESKTOP=1``). */
  desktop?: boolean;
  error: string | null;
  source: string | null;
  /** Markdown changelog for latest_version, null if not available. */
  release_notes: string | null;
  center_configured?: boolean;
  /** Names of the update-center variables missing from the server environment. */
  missing_configuration?: string[];
  install_available?: boolean;
  checked_at?: string | null;
  upgrade_phase?: UpgradePhase;
  upgrade_error?: string | null;
  upgrade_target?: string | null;
}

export interface RestartResponse {
  status: "restarting";
  service_mode: string;
}

export const updateApi = {
  getUpdateStatus: () => request<UpdateStatus>("/update/status"),
  checkForUpdates: () =>
    request<UpdateStatus>("/update/check", { method: "POST" }),
  upgrade: async (version: string): Promise<UpdateStatus> => {
    await request<{ status: "queued"; request_id: string; version: string }>(
      "/update/upgrade",
      {
        method: "POST",
        body: JSON.stringify({ version }),
      },
    );
    return request<UpdateStatus>("/update/status");
  },
  restartService: () =>
    request<RestartResponse>("/update/restart", { method: "POST" }),
};
