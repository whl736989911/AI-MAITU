import { routerBasename } from "../utils/publicUrl";

declare const BASE_URL: string;

/**
 * Get the full API URL with /api prefix
 * @param path - API path (e.g., "/models", "/skills")
 * @returns Full API URL (e.g., "http://localhost:8088/api/models" or "/api/models")
 */
export function getApiUrlForBase(path: string, base: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const normalizedBase = base.replace(/\/+$/, "");
  return `${normalizedBase}/api${normalizedPath}`;
}

export function getApiUrl(path: string): string {
  // An explicitly configured API origin wins; otherwise keep same-origin API
  // requests inside the Vite base path.
  const base = BASE_URL || routerBasename() || "";
  return getApiUrlForBase(path, base);
}

/**
 * Get a WebSocket URL derived from the same origin as the API.
 * Converts http:// → ws:// and https:// → wss://.
 * If BASE_URL is empty (same-origin), uses the current page location.
 * @param path - API path (e.g., "/browser-stream/ws")
 * @returns Full WebSocket URL
 */
export function getWsUrlForBase(
  path: string,
  base: string,
  protocol: string,
  host: string,
): string {
  const httpUrl = getApiUrlForBase(path, base);
  if (httpUrl.startsWith("http://")) return httpUrl.replace("http://", "ws://");
  if (httpUrl.startsWith("https://"))
    return httpUrl.replace("https://", "wss://");
  const wsProtocol = protocol === "https:" ? "wss:" : "ws:";
  return `${wsProtocol}//${host}${httpUrl}`;
}

export function getWsUrl(path: string): string {
  const base = BASE_URL || routerBasename() || "";
  return getWsUrlForBase(
    path,
    base,
    window.location.protocol,
    window.location.host,
  );
}
