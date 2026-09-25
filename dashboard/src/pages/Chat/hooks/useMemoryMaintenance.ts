import { useEffect, useRef, useState } from "react";
import { request } from "../../../api/request";

export type MemoryMaintenancePhase =
  | "idle"
  | "waiting"
  | "queued"
  | "backing_up"
  | "pruning"
  | "deduplicating"
  | "compacting"
  | "done"
  | "failed"
  | "skipped";

export interface MemoryMaintenanceStatus {
  kind?: "memory_slim" | string;
  phase: MemoryMaintenancePhase | string;
  percent?: number;
  detail?: string | null;
  file_bytes?: number | null;
  scanned?: number | null;
  total?: number | null;
  report?: Record<string, unknown> | null;
  error?: string | null;
  started_at?: number | null;
  updated_at?: number | null;
  skipped_reason?: string | null;
}

const ACTIVE: Record<string, true> = {
  waiting: true,
  queued: true,
  backing_up: true,
  pruning: true,
  deduplicating: true,
  compacting: true,
};
const TERMINAL: Record<string, true> = {
  done: true,
  failed: true,
  skipped: true,
};
const BLOCKING: Record<string, true> = {
  backing_up: true,
  deduplicating: true,
  compacting: true,
  pruning: true,
};
const TERMINAL_DISPLAY_SECONDS = 15;

export function useMemoryMaintenance(
  agentId: string | null | undefined,
  enabled: boolean,
) {
  const [status, setStatus] = useState<MemoryMaintenanceStatus | null>(null);
  const terminalSeenRef = useRef<{ key: string; timestamp: number } | null>(
    null,
  );

  useEffect(() => {
    terminalSeenRef.current = null;
    setStatus(null);
    if (!agentId || !enabled) {
      setStatus(null);
      return;
    }
    let stop = false;
    let timer: number | null = null;
    const pull = async () => {
      let nextDelay = 10_000;
      try {
        const row = await request<{
          memory_maintenance?: MemoryMaintenanceStatus | null;
        }>(`/agents/${agentId}/status`);
        const next = row.memory_maintenance ?? null;
        if (!stop) setStatus(next);
        const phase = next?.phase ?? "";
        if (TERMINAL[phase] && next) {
          const key = `${phase}:${next.updated_at ?? ""}:${
            next.started_at ?? ""
          }`;
          if (terminalSeenRef.current?.key !== key) {
            terminalSeenRef.current = {
              key,
              timestamp: Date.now() / 1000,
            };
          }
          const finishedAt =
            next.updated_at ?? terminalSeenRef.current.timestamp;
          const age = Date.now() / 1000 - finishedAt;
          if (age <= TERMINAL_DISPLAY_SECONDS) nextDelay = 2000;
        } else {
          terminalSeenRef.current = null;
          if (ACTIVE[phase]) nextDelay = 2000;
        }
      } catch {
        // Keep the last known state; a later single-flight poll can recover.
      } finally {
        if (!stop) timer = window.setTimeout(pull, nextDelay);
      }
    };
    void pull();
    return () => {
      stop = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [agentId, enabled]);

  const phase = status?.phase ?? "idle";
  const terminalAt = status?.updated_at ?? terminalSeenRef.current?.timestamp;
  const terminalAge = terminalAt ? Date.now() / 1000 - terminalAt : Infinity;
  return {
    status,
    visible:
      ACTIVE[phase] ||
      (TERMINAL[phase] && terminalAge <= TERMINAL_DISPLAY_SECONDS),
    blocking: !!BLOCKING[phase],
  };
}
