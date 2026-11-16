import { appPathname, publicUrl } from "../utils/publicUrl";

import { getApiUrl } from "./config";
import i18n from "../i18n";
import { markNavigatingAway } from "../utils/reloadOnStaleChunk";

const AUTH_TOKEN_KEY = "auth_token";
let serverTimeOffsetMs = 0;

/** Account deadlines follow the server clock, not the device's wall clock. */
export function serverNowMs(): number {
  return Date.now() + serverTimeOffsetMs;
}

/**
 * Fired when the session is no longer valid. Cancelable: a listener inside the
 * React tree calls ``preventDefault()`` to route to /login itself, which keeps
 * the SPA alive instead of tearing the document down mid-render.
 */
export const UNAUTHORIZED_EVENT = "octop:unauthorized";
/** Fired when an agent-scoped API request is forbidden. */
export const FORBIDDEN_EVENT = "octop:forbidden";
/** Fired when the server denies a business request because the instance is locked. */
export const INSTANCE_LICENSE_LOCKED_EVENT = "octop:instance-license-locked";
export const AUTH_IDENTITY_CHANGED_EVENT = "octop:auth-identity-changed";
let _redirectingForAccessExpiry = false;
let authIdentityGeneration = 0;

export function handleAccessExpired(
  noticeValue?: unknown,
  requestToken?: string,
  requestGeneration?: number,
): void {
  if (
    requestGeneration !== undefined &&
    (requestGeneration !== authIdentityGeneration ||
      !isSameAuthIdentity(requestToken ?? "", getAuthToken()))
  ) {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
  const notice = typeof noticeValue === "string" ? noticeValue : "";
  clearAuthToken();
  const path = appPathname(window.location.pathname);
  if (path.startsWith("/login") || _redirectingForAccessExpiry) return;
  _redirectingForAccessExpiry = true;
  markNavigatingAway();
  const fragment = new URLSearchParams({ access_notice: notice });
  window.location.replace(
    `${publicUrl("/login?oidc_error=access_expired")}#${fragment.toString()}`,
  );
}

function assertResponseIdentity(
  requestToken: string,
  requestGeneration: number,
): void {
  if (
    requestGeneration !== authIdentityGeneration ||
    !isSameAuthIdentity(requestToken, getAuthToken())
  ) {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
}

async function throwIfAccessExpired(
  response: Response,
  requestToken: string,
  requestGeneration: number,
): Promise<void> {
  if (response.status !== 403) return;
  const body = (await response
    .clone()
    .json()
    .catch(() => null)) as {
    error?: {
      code?: string;
      details?: { notice?: unknown; expires_at?: unknown };
    };
  } | null;
  assertResponseIdentity(requestToken, requestGeneration);
  if (body?.error?.code !== "ACCESS_EXPIRED") return;
  handleAccessExpired(
    body.error.details?.notice,
    requestToken,
    requestGeneration,
  );
}
export class InstanceLicenseLockedError extends Error {
  constructor() {
    super("Instance license locked");
    this.name = "InstanceLicenseLockedError";
  }
}

/**
 * A non-2xx response whose HTTP status the caller needs.
 *
 * The message stays identical to the plain Error this class replaces, so
 * existing text assertions keep working; download surfaces read `status` to
 * distinguish "file is gone" (404) from "disk is full" (507) from a retryable
 * network failure instead of collapsing every case into "please try again".
 */
export class HttpStatusError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "HttpStatusError";
    this.status = status;
  }
}

export const LICENSE_LOCKED_EVENT = "octop:license-locked";

function notifyLicenseLocked(path: string, status: number, body: string): void {
  if (status !== 423) return;
  try {
    const parsed = JSON.parse(body) as {
      error?: { code?: unknown };
      code?: unknown;
    };
    const code = parsed.error?.code ?? parsed.code;
    if (code === "INSTANCE_LICENSE_LOCKED" && typeof window !== "undefined") {
      window.dispatchEvent(
        new CustomEvent(LICENSE_LOCKED_EVENT, { detail: { path } }),
      );
    }
  } catch {
    // Non-JSON error bodies do not carry a machine-readable license code.
  }
}

/** Response header used by the server for JWT sliding renewal. */
export const ACCESS_TOKEN_RESPONSE_HEADER = "X-Octop-Access-Token";

/** Quiet error thrown when setup lockdown blocks a non-wizard API call. */
export class SetupRequiredError extends Error {
  constructor() {
    super("Setup required");
    this.name = "SetupRequiredError";
  }
}

/** Session flag: backend reported that first-run setup is still pending. */
let _setupRequiredKnown = false;

/** Mark that first-run setup is still required (skips further locked APIs). */
export function markSetupRequired(): void {
  _setupRequiredKnown = true;
}

/** Clear the local short-circuit so login or wizard recovery can recheck the server. */
export function clearSetupRequired(): void {
  _setupRequiredKnown = false;
}

/** Whether this tab already knows setup lockdown is active. */
export function isSetupRequiredKnown(): boolean {
  return _setupRequiredKnown;
}

function tokenSubject(token: string): string | null {
  const payload = token.split(".")[1];
  if (!payload) return null;
  try {
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    const claims = JSON.parse(new TextDecoder().decode(bytes)) as {
      sub?: unknown;
    };
    return typeof claims.sub === "string" && claims.sub.length > 0
      ? claims.sub
      : null;
  } catch {
    return null;
  }
}

function authIdentityChanged(
  previous: string | null,
  next: string | null,
): boolean {
  if (previous === next) return false;
  if (!previous || !next) return Boolean(previous || next);
  const previousSubject = tokenSubject(previous);
  const nextSubject = tokenSubject(next);
  return (
    previousSubject === null ||
    nextSubject === null ||
    previousSubject !== nextSubject
  );
}

/** Compare token identities for stale-response guards; same-sub renewals match. */
export function isSameAuthIdentity(previous: string, next: string): boolean {
  return !authIdentityChanged(previous, next);
}

function dispatchAuthIdentityChanged(): void {
  authIdentityGeneration += 1;
  window.dispatchEvent(new Event(AUTH_IDENTITY_CHANGED_EVENT));
}

let lastObservedAuthToken = "";
if (typeof window !== "undefined") {
  lastObservedAuthToken = localStorage.getItem(AUTH_TOKEN_KEY) || "";
  window.addEventListener("storage", (event) => {
    if (event.storageArea && event.storageArea !== localStorage) return;
    if (event.key === AUTH_TOKEN_KEY) {
      if (authIdentityChanged(event.oldValue, event.newValue)) {
        dispatchAuthIdentityChanged();
      }
      lastObservedAuthToken = event.newValue || "";
      return;
    }
    if (event.key === null) {
      const currentToken = localStorage.getItem(AUTH_TOKEN_KEY);
      if (lastObservedAuthToken || currentToken) {
        dispatchAuthIdentityChanged();
      }
      lastObservedAuthToken = currentToken || "";
    }
  });
}

/** Save JWT token to localStorage */
export function setAuthToken(token: string) {
  const previous = getAuthToken();
  const identityChanged = authIdentityChanged(previous, token);
  localStorage.setItem(AUTH_TOKEN_KEY, token);
  lastObservedAuthToken = token;
  clearSetupRequired();
  if (identityChanged) dispatchAuthIdentityChanged();
}

/** Get JWT token from localStorage */
export function getAuthToken(): string {
  return localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

/** Remove JWT token from localStorage */
export function clearAuthToken() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  lastObservedAuthToken = "";
  localStorage.removeItem("octop:active-agent");
  setActiveAgentId(null);
  dispatchAuthIdentityChanged();
}

/** Persist sliding renewal only while the original account is still active. */
export function applyRenewedAccessToken(
  response: Response,
  requestToken: string,
  requestGeneration: number,
): void {
  assertResponseIdentity(requestToken, requestGeneration);
  const renewed = response.headers.get(ACCESS_TOKEN_RESPONSE_HEADER);
  const current = getAuthToken();
  if (
    renewed &&
    current &&
    isSameAuthIdentity(requestToken, current) &&
    isSameAuthIdentity(current, renewed)
  ) {
    setAuthToken(renewed);
  }
}

let _redirectingToSetup = false;

/**
 * Hard-redirect once when the backend reports the wizard isn't done.
 * The flag prevents N parallel API calls from each issuing a navigate.
 */
function handleSetupRequired(): void {
  markSetupRequired();
  if (_redirectingToSetup) return;
  // Already on a public bootstrap route — a 503 from a background prefetch
  // must not reload the page or we loop forever.
  const path = appPathname(window.location.pathname);
  if (
    path.startsWith("/setup") ||
    path.startsWith("/login") ||
    path.startsWith("/invite")
  ) {
    return;
  }
  _redirectingToSetup = true;
  // Full reload drops any in-flight React state.
  window.location.replace(publicUrl("/setup"));
}

/** Wizard endpoints stay reachable while lockdown is active. */
function isSetupApiPath(path: string): boolean {
  return path === "/setup/status" || path.startsWith("/setup/");
}

/**
 * Skip the network when this tab already knows setup lockdown is on.
 * Avoids stampeding ``/api/agents`` etc. after the first 503 / status probe.
 */
function assertNotSetupLocked(path: string): void {
  if (_setupRequiredKnown && !isSetupApiPath(path)) {
    handleSetupRequired();
    throw new SetupRequiredError();
  }
}

/**
 * Inspect a response for the lockdown signal (503 + body
 * `{setup_required: true}`) and trigger a one-shot navigate to /setup.
 *
 * Returns ``true`` when the response matched and the caller should
 * abort the normal success/error path. The wizard's own ``/setup/*``
 * calls are exempt to avoid redirect loops.
 */
async function check503ForSetupRequired(
  path: string,
  response: Response,
  requestToken: string,
  requestGeneration: number,
): Promise<boolean> {
  if (response.status !== 503) return false;
  let body: unknown = null;
  try {
    body = await response.clone().json();
  } catch {
    /* not JSON — fall through to the standard error path. */
    assertResponseIdentity(requestToken, requestGeneration);
    return false;
  }
  assertResponseIdentity(requestToken, requestGeneration);
  if (
    body &&
    typeof body === "object" &&
    (body as Record<string, unknown>).setup_required === true
  ) {
    markSetupRequired();
    clearAuthToken();
    if (!isSetupApiPath(path)) {
      handleSetupRequired();
    }
    return true;
  }
  return false;
}

/**
 * Active agent id — populated by ``AgentProvider`` in ``context/AgentContext.tsx``
 * whenever the user picks a new agent in the top-bar switcher. Stored at
 * module scope so plain functions like ``request()`` can read it without
 * threading a context through every call site.
 *
 * The value is ALSO mirrored to ``localStorage["octop:active-agent"]`` by
 * the provider — but the source of truth at request time is this variable
 * so reactions stay synchronous.
 */
let activeAgentId: string | null = null;

/** Setter used by AgentProvider; also clears when ``null``. */
export function setActiveAgentId(id: string | null) {
  activeAgentId = id;
}

/** Read the active agent id (e.g. from non-React code). */
export function getActiveAgentId(): string | null {
  return activeAgentId;
}

/**
 * Decide whether a request path is "agent-scoped" — i.e. talking to a
 * concrete agent's resource — and therefore should carry the
 * ``X-Octop-Agent-Id`` header. Health, admin, auth, setup, providers, and
 * personas don't need it.
 */
function isAgentScopedPath(path: string): boolean {
  // Match `/agents/<id>/...` (one trailing segment after the id).
  // The path passed to request() is stripped of the /api prefix.
  if (/^\/agents\/[^/]+(\/|$)/.test(path)) return true;
  // MBTI endpoints that read/write the active agent's persona config.
  if (/^\/mbti\//.test(path)) return true;
  return false;
}

function buildHeaders(path: string, extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Accept-Language": i18n.language?.startsWith("zh") ? "zh" : "en",
  };

  // Apply the global JWT first; the caller's `extra` (including a
  // wizard token) can still override it below.
  const token = getAuthToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  // Caller-supplied headers win — needed so the setup wizard can pass
  // its short-TTL Bearer without being stomped by a stale localStorage
  // JWT.
  if (extra) {
    const extraEntries =
      extra instanceof Headers
        ? Array.from(extra.entries())
        : Array.isArray(extra)
        ? extra
        : Object.entries(extra);
    for (const [k, v] of extraEntries) {
      headers[k] = String(v);
    }
  }

  if (
    activeAgentId &&
    isAgentScopedPath(path) &&
    !headers["X-Octop-Agent-Id"]
  ) {
    headers["X-Octop-Agent-Id"] = activeAgentId;
  }

  return headers;
}

/**
 * Build auth-only headers (no Content-Type — let the browser set it for FormData).
 */
function buildAuthHeaders(path: string): Record<string, string> {
  const headers: Record<string, string> = {
    "Accept-Language": i18n.language?.startsWith("zh") ? "zh" : "en",
  };
  const token = getAuthToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (
    activeAgentId &&
    isAgentScopedPath(path) &&
    !headers["X-Octop-Agent-Id"]
  ) {
    headers["X-Octop-Agent-Id"] = activeAgentId;
  }
  return headers;
}

let _redirectingToLogin = false;

/**
 * Send the user to the login screen exactly once, no matter how many parallel
 * requests report an expired session.
 */
function handleUnauthorized(): void {
  if (_redirectingToLogin) return;
  const path = appPathname(window.location.pathname);
  if (
    path.startsWith("/setup") ||
    path.startsWith("/login") ||
    path.startsWith("/invite")
  )
    return;
  _redirectingToLogin = true;

  const takenOver = !window.dispatchEvent(
    new CustomEvent(UNAUTHORIZED_EVENT, { cancelable: true }),
  );
  if (takenOver) return;

  markNavigatingAway();
  window.location.replace(publicUrl("/login"));
}

/**
 * Handle 401 responses: clear token, redirect to login, and throw.
 * Shared by request(), requestBlob(), and requestUpload().
 */
async function throwIfUnauthorized(
  path: string,
  response: Response,
  requestToken: string,
  requestGeneration: number,
): Promise<void> {
  if (response.status !== 401) return;
  assertResponseIdentity(requestToken, requestGeneration);
  if (getAuthToken() !== requestToken) {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
  if (path.startsWith("/auth/")) return;
  let message = "Unauthorized";
  if (path.startsWith("/setup/")) {
    try {
      const body = (await response.clone().json()) as {
        error?: { message?: string };
      };
      if (body?.error?.message) {
        message = body.error.message;
      }
    } catch {
      /* keep generic message */
    }
  }
  assertResponseIdentity(requestToken, requestGeneration);
  if (getAuthToken() !== requestToken) {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
  clearAuthToken();
  handleUnauthorized();
  throw new Error(message);
}
async function throwIfLicenseLocked(
  response: Response,
  requestToken: string,
  requestGeneration: number,
): Promise<void> {
  if (response.status !== 403) return;
  let body: unknown;
  try {
    body = await response.clone().json();
  } catch {
    assertResponseIdentity(requestToken, requestGeneration);
    return;
  }
  assertResponseIdentity(requestToken, requestGeneration);
  if (!body || typeof body !== "object" || !("error" in body)) return;
  const error = body.error;
  if (
    !error ||
    typeof error !== "object" ||
    !("code" in error) ||
    error.code !== "INSTANCE_LICENSE_LOCKED"
  )
    return;
  window.dispatchEvent(new Event(INSTANCE_LICENSE_LOCKED_EVENT));
  throw new InstanceLicenseLockedError();
}

export async function request<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  assertNotSetupLocked(path);

  const requestToken = getAuthToken();
  const requestGeneration = authIdentityGeneration;
  const url = getApiUrl(path);
  const headers = buildHeaders(path, options.headers);
  const response = await fetch(url, {
    ...options,
    headers,
  });
  assertResponseIdentity(requestToken, requestGeneration);
  const serverTime = Date.parse(response.headers.get("Date") ?? "");
  if (Number.isFinite(serverTime)) {
    serverTimeOffsetMs = serverTime - Date.now();
  }
  await throwIfAccessExpired(response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);

  if (
    await check503ForSetupRequired(
      path,
      response,
      requestToken,
      requestGeneration,
    )
  ) {
    throw new SetupRequiredError();
  }

  await throwIfUnauthorized(path, response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);
  await throwIfLicenseLocked(response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);
  applyRenewedAccessToken(response, requestToken, requestGeneration);

  if (!response.ok) {
    if (response.status === 403 && isAgentScopedPath(path)) {
      assertResponseIdentity(requestToken, requestGeneration);
      window.dispatchEvent(
        new CustomEvent(FORBIDDEN_EVENT, { detail: { path } }),
      );
    }
    const text = await response.text().catch(() => "");
    assertResponseIdentity(requestToken, requestGeneration);
    notifyLicenseLocked(path, response.status, text);
    throw new Error(
      `Request failed: ${response.status} ${response.statusText}${
        text ? ` - ${text}` : ""
      }`,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const text = await response.text();
    assertResponseIdentity(requestToken, requestGeneration);
    return text as unknown as T;
  }

  const result = (await response.json()) as T;
  assertResponseIdentity(requestToken, requestGeneration);
  return result;
}

/**
 * Download a binary resource as a Blob.
 *
 * ``onProgress`` (when the server sends ``Content-Length``) streams the body
 * and reports ``(loaded, total)`` in bytes — used by the PDF preview's
 * download progress bar.
 */
export async function requestBlob(
  path: string,
  options: RequestInit = {},
  onProgress?: (loaded: number, total: number) => void,
): Promise<Blob> {
  assertNotSetupLocked(path);

  const requestToken = getAuthToken();
  const requestGeneration = authIdentityGeneration;
  const url = getApiUrl(path);
  const headers = buildAuthHeaders(path);
  const response = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string>) },
  });
  assertResponseIdentity(requestToken, requestGeneration);
  await throwIfAccessExpired(response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);

  if (
    await check503ForSetupRequired(
      path,
      response,
      requestToken,
      requestGeneration,
    )
  ) {
    throw new SetupRequiredError();
  }

  await throwIfUnauthorized(path, response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);
  await throwIfLicenseLocked(response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);
  applyRenewedAccessToken(response, requestToken, requestGeneration);

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    assertResponseIdentity(requestToken, requestGeneration);
    notifyLicenseLocked(path, response.status, text);
    throw new HttpStatusError(
      response.status,
      `Request failed: ${response.status} ${response.statusText}${
        text ? ` - ${text}` : ""
      }`,
    );
  }

  const total = Number(response.headers.get("content-length")) || 0;
  if (onProgress && total > 0 && response.body) {
    const reader = response.body.getReader();
    const chunks: BlobPart[] = [];
    let loaded = 0;
    for (;;) {
      const { done, value } = await reader.read();
      assertResponseIdentity(requestToken, requestGeneration);
      if (done) break;
      if (value) {
        chunks.push(value as unknown as BlobPart);
        loaded += value.byteLength;
        onProgress(loaded, total);
      }
    }
    return new Blob(chunks);
  }

  const blob = await response.blob();
  assertResponseIdentity(requestToken, requestGeneration);
  return blob;
}

/**
 * Authenticated GET that only checks success — cancels the body without
 * buffering it (existence probes for large workspace files).
 */
export async function probeAuthResource(
  path: string,
  options: RequestInit = {},
): Promise<void> {
  assertNotSetupLocked(path);

  const requestToken = getAuthToken();
  const requestGeneration = authIdentityGeneration;
  const url = getApiUrl(path);
  const headers = buildAuthHeaders(path);
  const response = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string>) },
  });
  assertResponseIdentity(requestToken, requestGeneration);

  if (
    await check503ForSetupRequired(
      path,
      response,
      requestToken,
      requestGeneration,
    )
  ) {
    throw new SetupRequiredError();
  }
  await throwIfAccessExpired(response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);

  await throwIfUnauthorized(path, response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);
  await throwIfLicenseLocked(response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);
  applyRenewedAccessToken(response, requestToken, requestGeneration);

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    assertResponseIdentity(requestToken, requestGeneration);
    notifyLicenseLocked(path, response.status, text);
    throw new Error(
      `Request failed: ${response.status} ${response.statusText}${
        text ? ` - ${text}` : ""
      }`,
    );
  }

  try {
    await response.body?.cancel();
  } catch {
    /* ignore cancel failures */
  }
  assertResponseIdentity(requestToken, requestGeneration);
}

/**
 * POST JSON and hand back the response body as a byte stream (chunked TTS).
 * Mirrors requestBlob()'s auth/setup/401 handling but never buffers.
 */
export async function requestStream(
  path: string,
  options: RequestInit = {},
): Promise<{ contentType: string; body: ReadableStream<Uint8Array> }> {
  assertNotSetupLocked(path);

  const requestToken = getAuthToken();
  const requestGeneration = authIdentityGeneration;
  const url = getApiUrl(path);
  const headers = buildAuthHeaders(path);
  const response = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string>) },
  });
  assertResponseIdentity(requestToken, requestGeneration);

  if (
    await check503ForSetupRequired(
      path,
      response,
      requestToken,
      requestGeneration,
    )
  ) {
    throw new SetupRequiredError();
  }
  await throwIfAccessExpired(response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);

  await throwIfUnauthorized(path, response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);
  await throwIfLicenseLocked(response, requestToken, requestGeneration);
  assertResponseIdentity(requestToken, requestGeneration);
  applyRenewedAccessToken(response, requestToken, requestGeneration);

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    assertResponseIdentity(requestToken, requestGeneration);
    notifyLicenseLocked(path, response.status, text);
    throw new Error(
      `Request failed: ${response.status} ${response.statusText}${
        text ? ` - ${text}` : ""
      }`,
    );
  }

  if (!response.body) {
    throw new Error("Empty stream from server");
  }

  assertResponseIdentity(requestToken, requestGeneration);
  return {
    contentType: response.headers.get("content-type") || "",
    body: response.body,
  };
}

export type UploadProgressHandler = (percent: number) => void;

/**
 * Upload a FormData payload (no explicit Content-Type — browser handles boundary).
 * Uses XMLHttpRequest so callers can report upload progress.
 */
export async function requestUpload<T = unknown>(
  path: string,
  body: FormData,
  options: RequestInit = {},
  onProgress?: UploadProgressHandler,
): Promise<T> {
  assertNotSetupLocked(path);

  const requestToken = getAuthToken();
  const requestGeneration = authIdentityGeneration;
  const url = getApiUrl(path);
  const headers = buildAuthHeaders(path);
  const method = options.method ?? "POST";

  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);

    for (const [key, value] of Object.entries(headers)) {
      xhr.setRequestHeader(key, value);
    }
    if (options.headers) {
      const extraEntries =
        options.headers instanceof Headers
          ? Array.from(options.headers.entries())
          : Array.isArray(options.headers)
          ? options.headers
          : Object.entries(options.headers);
      for (const [k, v] of extraEntries) {
        xhr.setRequestHeader(k, String(v));
      }
    }

    if (onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
    }

    if (options.signal) {
      if (options.signal.aborted) {
        reject(new DOMException("The operation was aborted.", "AbortError"));
        return;
      }
      options.signal.addEventListener(
        "abort",
        () => {
          xhr.abort();
        },
        { once: true },
      );
    }

    xhr.onload = () => {
      void (async () => {
        try {
          const status = xhr.status;
          const responseText = xhr.responseText;
          const responseHeaders = new Headers();
          const renewed = xhr.getResponseHeader(ACCESS_TOKEN_RESPONSE_HEADER);
          if (renewed) {
            responseHeaders.set(ACCESS_TOKEN_RESPONSE_HEADER, renewed);
          }
          const response = new Response(responseText, {
            status,
            headers: responseHeaders,
          });
          assertResponseIdentity(requestToken, requestGeneration);
          await throwIfAccessExpired(response, requestToken, requestGeneration);
          assertResponseIdentity(requestToken, requestGeneration);

          if (
            await check503ForSetupRequired(
              path,
              response,
              requestToken,
              requestGeneration,
            )
          ) {
            reject(new SetupRequiredError());
            return;
          }

          await throwIfUnauthorized(
            path,
            response,
            requestToken,
            requestGeneration,
          );
          assertResponseIdentity(requestToken, requestGeneration);
          applyRenewedAccessToken(response, requestToken, requestGeneration);

          if (!response.ok) {
            assertResponseIdentity(requestToken, requestGeneration);
            notifyLicenseLocked(path, status, responseText);
            // Same shape as request() so parseApiError() can read the error envelope.
            reject(
              new Error(
                `Upload failed: ${status}${
                  responseText ? ` - ${responseText}` : ""
                }`,
              ),
            );
            return;
          }

          let result: T;
          try {
            result = (await response.json()) as T;
          } catch {
            assertResponseIdentity(requestToken, requestGeneration);
            reject(new Error("Upload failed: invalid JSON response"));
            return;
          }
          assertResponseIdentity(requestToken, requestGeneration);
          resolve(result);
        } catch (err) {
          reject(err);
        }
      })();
    };

    xhr.onerror = () => reject(new Error("Network error"));
    xhr.onabort = () =>
      reject(new DOMException("The operation was aborted.", "AbortError"));

    xhr.send(body);
  });
}
