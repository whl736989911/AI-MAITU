/**
 * Download an authenticated Octop API file URL via blob (Bearer token),
 * instead of navigating the browser tab (which would drop JWT and land on the SPA).
 */

import {
  useCallback,
  useState,
  type CSSProperties,
  type MouseEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { HttpStatusError, requestBlob } from "../api/request";
import { isDataUrl, needsAuthBlobFetch } from "../utils/toolMediaBlocks";

import { message as antMessage } from "@/utils/antdMessage";

function apiPathForBlobFetch(fetchUrl: string): string {
  if (fetchUrl.startsWith("http")) {
    const parsed = new URL(fetchUrl);
    return parsed.pathname.replace(/^\/api/, "") + parsed.search;
  }
  return fetchUrl.replace(/^\/api/, "");
}

function filenameFromUrl(url: string): string | undefined {
  try {
    const parsed = url.startsWith("http")
      ? new URL(url)
      : new URL(url, "http://maitu.local");
    const pathParam = parsed.searchParams.get("path");
    if (pathParam) {
      const name = pathParam.split("/").pop();
      if (name) return decodeURIComponent(name);
    }
    const source = parsed.searchParams.get("source");
    if (source) {
      const name = source.split("/").pop();
      if (name) return decodeURIComponent(name);
    }
    const last = parsed.pathname.split("/").pop();
    return last ? decodeURIComponent(last) : undefined;
  } catch {
    return undefined;
  }
}

/** True when a Markdown / anchor href needs authenticated blob download. */
export function isAuthDownloadHref(href: string | undefined | null): boolean {
  if (!href) return false;
  const trimmed = href.trim();
  if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith("mailto:")) {
    return false;
  }
  try {
    return needsAuthBlobFetch(trimmed);
  } catch {
    return false;
  }
}

export async function downloadAuthFile(
  url: string,
  options?: { filename?: string },
): Promise<void> {
  let blob: Blob;
  if (isDataUrl(url)) {
    const res = await fetch(url);
    blob = await res.blob();
  } else {
    blob = await requestBlob(apiPathForBlobFetch(url));
  }

  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objUrl;
  a.download = options?.filename || filenameFromUrl(url) || "download";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(objUrl), 100);
}

/**
 * Name the real reason. A download that 404s or hits a full disk will never
 * succeed by retrying, so those two say what happened; only an unclassified
 * failure keeps the retryable wording.
 */
export function downloadErrorMessage(error: unknown, t: TFunction): string {
  const status = error instanceof HttpStatusError ? error.status : 0;
  if (status === 404) {
    return t("chat.downloadFailedNotFound", "文件未生成或已被删除");
  }
  if (status === 507) {
    return t("chat.downloadFailedDiskFull", "磁盘空间不足，请联系管理员");
  }
  return t("chat.downloadFailed", "下载失败，请重试");
}

const defaultLinkStyle: CSSProperties = {
  background: "none",
  border: "none",
  padding: 0,
  cursor: "pointer",
  color: "inherit",
  textDecoration: "underline",
  font: "inherit",
};

export function AuthFileDownloadLink({
  url,
  filename,
  children,
  className,
  style,
}: {
  url: string;
  filename?: string;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);

  const handleClick = useCallback(
    async (event: MouseEvent) => {
      event.preventDefault();
      event.stopPropagation();
      setLoading(true);
      try {
        await downloadAuthFile(url, { filename });
      } catch (error) {
        antMessage.error(downloadErrorMessage(error, t));
      } finally {
        setLoading(false);
      }
    },
    [url, filename, t],
  );

  const label = children ?? filename ?? filenameFromUrl(url) ?? url;

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={loading}
      className={className}
      style={{
        ...defaultLinkStyle,
        cursor: loading ? "wait" : "pointer",
        ...style,
      }}
    >
      {label}
    </button>
  );
}
