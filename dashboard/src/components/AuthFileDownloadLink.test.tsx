/**
 * A download that cannot succeed must say why: retrying a deleted file or a
 * full disk never helps, so only an unclassified failure keeps "try again".
 */

import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";

import { HttpStatusError } from "../api/request";
import { downloadErrorMessage } from "./AuthFileDownloadLink";

const t = ((key: string) => key) as unknown as TFunction;

describe("download failure wording", () => {
  it("names a missing file instead of asking for a retry", () => {
    expect(
      downloadErrorMessage(new HttpStatusError(404, "Request failed"), t),
    ).toBe("chat.downloadFailedNotFound");
  });

  it("names a full disk", () => {
    expect(
      downloadErrorMessage(new HttpStatusError(507, "Request failed"), t),
    ).toBe("chat.downloadFailedDiskFull");
  });

  it("keeps the retry wording for an unclassified failure", () => {
    expect(downloadErrorMessage(new Error("offline"), t)).toBe(
      "chat.downloadFailed",
    );
    expect(
      downloadErrorMessage(new HttpStatusError(500, "Request failed"), t),
    ).toBe("chat.downloadFailed");
  });
});
