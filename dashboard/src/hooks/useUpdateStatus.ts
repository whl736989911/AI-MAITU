import { useCallback, useEffect, useState } from "react";
import { updateApi, type UpdateStatus } from "../api/modules/update";
import {
  UPDATE_STATUS_CHANGED_EVENT,
  UPDATE_STATUS_POLL_MS,
  isUpdateStatusCacheExpired,
  readStoredUpdateStatus,
  storeUpdateStatus,
} from "../utils/updateStatusCache";

/** Shared in-flight request for the current installed version. */
let inFlight: Promise<UpdateStatus | null> | null = null;

async function probeUpdateStatus(): Promise<UpdateStatus | null> {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    try {
      const next = await updateApi.getUpdateStatus();
      storeUpdateStatus(next);
      return next;
    } catch {
      return null;
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}

export function useUpdateStatus() {
  const [status, setStatus] = useState<UpdateStatus | null>(() =>
    readStoredUpdateStatus(),
  );
  const [isUnavailable, setIsUnavailable] = useState(false);

  const refreshStatus = useCallback(async (force = false) => {
    if (!force && !isUpdateStatusCacheExpired()) {
      const cached = readStoredUpdateStatus();
      if (cached) {
        setStatus(cached);
        setIsUnavailable(false);
        return;
      }
    }
    const next = await probeUpdateStatus();
    if (next) {
      setStatus(next);
      setIsUnavailable(false);
    } else {
      setIsUnavailable(true);
    }
  }, []);

  useEffect(() => {
    // Dashboard boot: always re-fetch so a restarted server is not hidden
    // by a still-fresh localStorage entry. Focus / interval still honor TTL.
    void refreshStatus(true);
  }, [refreshStatus]);

  useEffect(() => {
    const onFocus = () => {
      void refreshStatus(false);
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshStatus]);

  // Refresh the installed version while the dashboard stays open.
  useEffect(() => {
    const id = window.setInterval(() => {
      void refreshStatus(false);
    }, UPDATE_STATUS_POLL_MS);
    return () => window.clearInterval(id);
  }, [refreshStatus]);
  const upgradeActive =
    status?.upgrade_phase === "queued" ||
    status?.upgrade_phase === "downloading" ||
    status?.upgrade_phase === "validating" ||
    status?.upgrade_phase === "building" ||
    status?.upgrade_phase === "backing_up" ||
    status?.upgrade_phase === "stopping" ||
    status?.upgrade_phase === "installing" ||
    status?.upgrade_phase === "restarting" ||
    status?.upgrade_phase === "verifying";

  useEffect(() => {
    if (!upgradeActive) return;
    const id = window.setInterval(() => {
      void refreshStatus(true);
    }, 2500);
    return () => window.clearInterval(id);
  }, [refreshStatus, upgradeActive]);

  // Sync Header / Sidebar / Update page when any writer stores a new status.
  useEffect(() => {
    const onChanged = (event: Event) => {
      const next = (event as CustomEvent<UpdateStatus>).detail;
      if (next) {
        setStatus(next);
        setIsUnavailable(false);
      }
    };
    window.addEventListener(UPDATE_STATUS_CHANGED_EVENT, onChanged);
    return () =>
      window.removeEventListener(UPDATE_STATUS_CHANGED_EVENT, onChanged);
  }, []);

  return { status, refreshStatus, isUnavailable };
}
