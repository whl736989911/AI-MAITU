import { request } from "../request";

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
}

export interface RestartResponse {
  status: "restarting";
  service_mode: string;
}

export const updateApi = {
  getUpdateStatus: () => request<UpdateStatus>("/update/status"),
  restartService: () =>
    request<RestartResponse>("/update/restart", { method: "POST" }),
};
