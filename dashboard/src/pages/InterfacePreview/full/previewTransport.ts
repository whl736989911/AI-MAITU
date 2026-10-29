import zh from "../../../locales/zh.json";
import en from "../../../locales/en.json";
import { installPreviewXhr } from "./previewXhr";

export type PreviewApiHandler = (
  request: Request,
) => Response | undefined | Promise<Response | undefined>;

export const PREVIEW_BLOCKED_EVENT = "maitu:preview-blocked-operation";
export interface PreviewBlockedOperation {
  method: string;
  path: string;
}

const handlers = new Map<string, PreviewApiHandler>();
let installed = false;

export function registerPreviewApi(
  key: string,
  handler: PreviewApiHandler,
): void {
  handlers.set(key, handler);
}

function isBusinessUrl(url: URL): boolean {
  return url.pathname === "/api" || url.pathname.startsWith("/api/");
}

function isExternalUrl(url: URL): boolean {
  return (
    (url.protocol === "http:" || url.protocol === "https:") &&
    url.origin !== window.location.origin
  );
}

function reportBlocked(method: string, url: URL): void {
  window.dispatchEvent(
    new CustomEvent<PreviewBlockedOperation>(PREVIEW_BLOCKED_EVENT, {
      detail: { method, path: url.pathname },
    }),
  );
}

/** Installed only by the independent HTML entry; never forward business calls. */
export function installPreviewTransport(
  locale: () => string = () => "zh",
): void {
  if (installed) return;
  installed = true;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const source = input instanceof Request ? input.url : String(input);
    const url = new URL(source, window.location.href);
    if (!isBusinessUrl(url) && !isExternalUrl(url))
      return nativeFetch(input, init);
    const effectiveRequest =
      input instanceof Request
        ? new Request(input, init)
        : new Request(url, init);
    if (!isExternalUrl(url)) {
      for (const handler of handlers.values()) {
        const response = await handler(effectiveRequest.clone());
        if (response) return response;
      }
      if (
        effectiveRequest.method === "GET" &&
        url.pathname === "/api/settings/timezone"
      ) {
        return Response.json({ timezone: "Asia/Shanghai" });
      }
      if (
        effectiveRequest.method === "GET" &&
        url.pathname === "/api/settings/upload"
      ) {
        return Response.json({
          max_upload_mb: 20,
          max_upload_bytes: 20 * 1024 * 1024,
        });
      }
      if (
        effectiveRequest.method === "GET" &&
        url.pathname === "/api/voice/active"
      ) {
        return Response.json({ stt: "browser", tts: "browser" });
      }
    }
    reportBlocked(effectiveRequest.method, url);
    const copy = locale().startsWith("zh") ? zh : en;
    const message = copy.interfacePreview.full.common.noExternalExecution;
    return Response.json(
      { error: { code: "PREVIEW_ONLY", message }, message },
      { status: 409 },
    );
  };
  installPreviewXhr((url) => isBusinessUrl(url) || isExternalUrl(url));

  document.addEventListener(
    "click",
    (event) => {
      if (!(event.target instanceof Element)) return;
      const anchor = event.target.closest<HTMLAnchorElement>("a[href]");
      if (!anchor) return;
      const url = new URL(anchor.href, window.location.href);
      if (!isBusinessUrl(url) && !isExternalUrl(url)) return;
      event.preventDefault();
      event.stopPropagation();
      reportBlocked("GET", url);
    },
    true,
  );

  const nativeOpen = window.open.bind(window);
  window.open = (url, target, features) => {
    const parsed = new URL(String(url ?? "about:blank"), window.location.href);
    if (isBusinessUrl(parsed) || isExternalUrl(parsed)) {
      reportBlocked("OPEN", parsed);
      return null;
    }
    return nativeOpen(url, target, features);
  };
}
