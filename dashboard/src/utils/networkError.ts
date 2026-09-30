export function isNetworkFetchError(error: unknown): boolean {
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return true;
  }
  const name =
    error instanceof Error
      ? error.name
      : error && typeof error === "object" && "name" in error
      ? String(error.name)
      : "";
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
      ? error
      : String(error ?? "");
  if (
    /failed to fetch|networkerror|network request failed|load failed|err_network|err_internet_disconnected|err_connection|econnrefused|enotfound/i.test(
      message,
    )
  ) {
    return true;
  }
  return name === "TypeError" && /fetch|load failed|network/i.test(message);
}
