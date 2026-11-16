export type DashboardPushEvent = {
  type: "dashboard_push";
  agent_id: string;
  thread_id: string;
  text: string;
  agent_name?: string;
};

export function parseDashboardPushFrame(
  raw: unknown,
): DashboardPushEvent | null {
  if (raw === null || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  if (obj.type !== "dashboard_push") return null;
  const agentId = typeof obj.agent_id === "string" ? obj.agent_id.trim() : "";
  const threadId =
    typeof obj.thread_id === "string" ? obj.thread_id.trim() : "";
  const text = typeof obj.text === "string" ? obj.text : "";
  if (!agentId || !threadId || !text.trim()) return null;
  const name = typeof obj.agent_name === "string" ? obj.agent_name.trim() : "";
  return {
    type: "dashboard_push",
    agent_id: agentId,
    thread_id: threadId,
    text,
    ...(name ? { agent_name: name } : {}),
  };
}

export function truncatePushText(text: string, max = 240): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max)}…`;
}

export type SystemNotifyPermission =
  | "granted"
  | "denied"
  | "default"
  | "unsupported";

/** System notifications are only available with an already granted permission. */
export function readSystemNotifyPermission(
  isSecureContext: boolean,
  notification: { permission: string } | undefined,
): SystemNotifyPermission {
  if (!isSecureContext || !notification) return "unsupported";
  if (
    notification.permission === "granted" ||
    notification.permission === "denied" ||
    notification.permission === "default"
  ) {
    return notification.permission;
  }
  return "unsupported";
}

export function shouldShowSystemPush(
  permission: SystemNotifyPermission,
  visible: boolean,
): boolean {
  return permission === "granted" && !visible;
}

/** Dedupe identity spans reconnects/tabs for the same content in a thread. */
export function pushDedupeKey(
  event: Pick<DashboardPushEvent, "thread_id" | "text">,
): string {
  return `${event.thread_id}\n${event.text}`;
}

export function systemNotificationTag(threadId: string): string {
  return `dash-push:${threadId}`;
}

/** How long a claimed push suppresses duplicate OS notifications across tabs. */
export const PUSH_DEDUPE_TTL_MS = 2_000;

const HANDLED_PREFIX = "octop-push-handled:";
const LOCK_PREFIX = "octop-push-lock:";

function hashPushKey(key: string): string {
  let hash = 5381;
  for (let i = 0; i < key.length; i++) {
    hash = Math.imul(hash, 33) ^ key.charCodeAt(i);
  }
  return (hash >>> 0).toString(36);
}

function storedKey(prefix: string, key: string): string {
  return `${prefix}${hashPushKey(key)}`;
}

function isFresh(raw: string | null, now: number, ttlMs: number): boolean {
  if (!raw) return false;
  const timestamp = Number(raw.split("@")[0]);
  return (
    Number.isFinite(timestamp) && timestamp <= now && now - timestamp < ttlMs
  );
}

function sweepPushState(storage: Storage, now: number): void {
  const stale: string[] = [];
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (
      !key ||
      (!key.startsWith(HANDLED_PREFIX) && !key.startsWith(LOCK_PREFIX))
    ) {
      continue;
    }
    if (!isFresh(storage.getItem(key), now, PUSH_DEDUPE_TTL_MS)) {
      stale.push(key);
    }
  }
  for (const key of stale) storage.removeItem(key);
}
export function clearSystemPushClaims(storage: Storage | null): void {
  if (!storage) return;
  try {
    const keys: string[] = [];
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i);
      if (key?.startsWith(HANDLED_PREFIX) || key?.startsWith(LOCK_PREFIX)) {
        keys.push(key);
      }
    }
    for (const key of keys) storage.removeItem(key);
  } catch {
    // Storage can be unavailable in private browsing or restricted contexts.
  }
}

export function wasPushHandled(
  storage: Storage,
  key: string,
  now = Date.now(),
  ttlMs = PUSH_DEDUPE_TTL_MS,
): boolean {
  return isFresh(storage.getItem(storedKey(HANDLED_PREFIX, key)), now, ttlMs);
}

export function claimSystemPush(
  storage: Storage | null,
  key: string,
  tabId: string,
  now = Date.now(),
  ttlMs = PUSH_DEDUPE_TTL_MS,
): boolean {
  if (!storage) return false;
  try {
    sweepPushState(storage, now);
    if (wasPushHandled(storage, key, now, ttlMs)) return false;

    const lockKey = storedKey(LOCK_PREFIX, key);
    const existingLock = storage.getItem(lockKey);
    if (isFresh(existingLock, now, ttlMs)) return false;

    // localStorage is shared across same-origin tabs. Read-back ensures only the
    // last writer may proceed if two tabs race between reading and setting.
    const lock = `${now}@${tabId}`;
    storage.setItem(lockKey, lock);
    if (storage.getItem(lockKey) !== lock) return false;
    storage.setItem(storedKey(HANDLED_PREFIX, key), String(now));
    return true;
  } catch {
    return false;
  }
}
