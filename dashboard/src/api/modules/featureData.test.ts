/**
 * featureData.test.ts — the wire contract of the business-data API module.
 *
 * The panel is only as correct as the paths and bodies it sends, so those are
 * pinned here: a read addresses the object's records under the feature's agent,
 * a write carries the revision it saw, and an attachment upload is multipart
 * (FormData, never a JSON field). Downloads go through the authenticated blob
 * helper so a token never lands in a URL.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const { request, requestBlob, requestUpload } = vi.hoisted(() => ({
  request: vi.fn(),
  requestBlob: vi.fn(),
  requestUpload: vi.fn(),
}));

vi.mock("../request", () => ({ request, requestBlob, requestUpload }));

import { featureDataApi } from "./featureData";

beforeEach(() => {
  request.mockReset();
  requestBlob.mockReset();
  requestUpload.mockReset();
  request.mockResolvedValue({ schema: null, error: null });
  requestBlob.mockResolvedValue(new Blob());
  requestUpload.mockResolvedValue({ id: "file-1" });
});

describe("featureDataApi schema", () => {
  it("reads and writes the schema document", async () => {
    await featureDataApi.getSchema("feat/a");
    await featureDataApi.putSchema("feat/a", { version: 1, objects: [] });

    expect(request).toHaveBeenNthCalledWith(
      1,
      "/agents/feat%2Fa/feature-data/schema",
    );
    expect(request).toHaveBeenNthCalledWith(
      2,
      "/agents/feat%2Fa/feature-data/schema",
      {
        method: "PUT",
        body: JSON.stringify({ schema: { version: 1, objects: [] } }),
      },
    );
  });
});

describe("featureDataApi records", () => {
  it("pages one object's records with the search term", async () => {
    request.mockResolvedValueOnce({ records: [], total: 0 });

    await featureDataApi.listRecords("agent-1", "supplier", {
      q: "  ACME  ",
      limit: 20,
      offset: 40,
    });

    expect(request).toHaveBeenCalledWith(
      "/agents/agent-1/feature-data/supplier/records?q=ACME&limit=20&offset=40",
    );
  });

  it("omits an empty search term and default paging", async () => {
    request.mockResolvedValueOnce({ records: [], total: 0 });

    await featureDataApi.listRecords("agent-1", "supplier", { q: "   " });

    expect(request).toHaveBeenCalledWith(
      "/agents/agent-1/feature-data/supplier/records",
    );
  });
  it("sends structured AND filters and sorting without changing pagination", async () => {
    await featureDataApi.listRecords("agent-1", "supplier", {
      filters: [
        { field: "weight", op: "gte", value: 100 },
        { field: "made_on", op: "lte", value: "2026-10-23" },
      ],
      sort_by: "made_on",
      sort_order: "asc",
      limit: 10,
      offset: 20,
    });
    const path = request.mock.calls[0][0] as string;
    const params = new URLSearchParams(path.split("?")[1]);
    expect(
      path.startsWith("/agents/agent-1/feature-data/supplier/records?"),
    ).toBe(true);
    expect(JSON.parse(params.get("filters") ?? "[]")).toEqual([
      { field: "weight", op: "gte", value: 100 },
      { field: "made_on", op: "lte", value: "2026-10-23" },
    ]);
    expect(params.get("sort_by")).toBe("made_on");
    expect(params.get("sort_order")).toBe("asc");
    expect(params.get("limit")).toBe("10");
    expect(params.get("offset")).toBe("20");
  });

  it("loads authorized reference choices and hands responsibility over", async () => {
    await featureDataApi.listRecordReferenceOptions("agent-1", {
      target_agent_id: "agent-2",
      object_key: "contracts",
      q: "ACME",
      limit: 25,
    });
    await featureDataApi.handover("agent-1", {
      from_user_id: 7,
      to_user_id: 12,
      object_key: "supplier",
    });
    expect(request).toHaveBeenNthCalledWith(
      1,
      "/agents/agent-1/feature-data/references/records?target_agent_id=agent-2&object_key=contracts&q=ACME&limit=25",
    );
    expect(request).toHaveBeenNthCalledWith(
      2,
      "/agents/agent-1/feature-data/handover",
      {
        method: "POST",
        body: JSON.stringify({
          from_user_id: 7,
          to_user_id: 12,
          object_key: "supplier",
        }),
      },
    );
  });
  it("uses feature-scoped principals so normal feature callers need no user-management API", async () => {
    await featureDataApi.listPrincipals("agent-1", { q: "ada", limit: 200 });
    await featureDataApi.createRecord("agent-1", "supplier", { name: "ACME" });
    await featureDataApi.updateRecord(
      "agent-1",
      "rec-1",
      { name: "ACME Ltd" },
      1,
    );
    expect(request).toHaveBeenNthCalledWith(
      1,
      "/agents/agent-1/feature-data/principals?q=ada&limit=200",
    );
    expect(request.mock.calls.some(([path]) => path === "/users")).toBe(false);
    expect(request).toHaveBeenCalledWith(
      "/agents/agent-1/feature-data/supplier/records",
      { method: "POST", body: JSON.stringify({ values: { name: "ACME" } }) },
    );
    expect(request).toHaveBeenCalledWith(
      "/agents/agent-1/feature-data/records/rec-1",
      {
        method: "PATCH",
        body: JSON.stringify({
          values: { name: "ACME Ltd" },
          expected_revision: 1,
        }),
      },
    );
  });

  it("creates, updates and publishes a record", async () => {
    await featureDataApi.createRecord("agent-1", "supplier", { name: "ACME" });
    await featureDataApi.updateRecord(
      "agent-1",
      "rec-1",
      { name: "ACME 2" },
      3,
    );
    await featureDataApi.publishRecord("agent-1", "rec-1");
    await featureDataApi.deleteRecord("agent-1", "rec-1");

    expect(request).toHaveBeenNthCalledWith(
      1,
      "/agents/agent-1/feature-data/supplier/records",
      {
        method: "POST",
        body: JSON.stringify({ values: { name: "ACME" } }),
      },
    );
    expect(request).toHaveBeenNthCalledWith(
      2,
      "/agents/agent-1/feature-data/records/rec-1",
      {
        method: "PATCH",
        body: JSON.stringify({
          values: { name: "ACME 2" },
          expected_revision: 3,
        }),
      },
    );
    expect(request).toHaveBeenNthCalledWith(
      3,
      "/agents/agent-1/feature-data/records/rec-1/publish",
      { method: "POST" },
    );
    expect(request).toHaveBeenNthCalledWith(
      4,
      "/agents/agent-1/feature-data/records/rec-1",
      { method: "DELETE" },
    );
  });

  it("answers the history as a list whether it arrives wrapped or bare", async () => {
    request.mockResolvedValueOnce({ changes: [{ id: "c1", created_at: 1 }] });
    request.mockResolvedValueOnce([{ id: "c2", created_at: 2 }]);

    const wrapped = await featureDataApi.history("agent-1", "rec-1");
    const bare = await featureDataApi.history("agent-1", "rec-1");

    expect(wrapped.map((item) => item.id)).toEqual(["c1"]);
    expect(bare.map((item) => item.id)).toEqual(["c2"]);
    expect(request).toHaveBeenNthCalledWith(
      1,
      "/agents/agent-1/feature-data/records/rec-1/history?limit=20",
    );
  });
});

describe("featureDataApi attachments", () => {
  it("uploads a version as multipart FormData with an optional note", async () => {
    const file = new File(["bytes"], "license.pdf", {
      type: "application/pdf",
    });

    await featureDataApi.uploadFile(
      "agent-1",
      "rec-1",
      "license",
      file,
      "renewed",
    );

    expect(requestUpload).toHaveBeenCalledTimes(1);
    const [path, form] = requestUpload.mock.calls[0] as [string, FormData];
    expect(path).toBe(
      "/agents/agent-1/feature-data/records/rec-1/files/license",
    );
    expect(form).toBeInstanceOf(FormData);
    expect((form.get("file") as File).name).toBe("license.pdf");
    expect(form.get("note")).toBe("renewed");
  });

  it("omits a blank note instead of sending an empty field", async () => {
    const file = new File(["bytes"], "license.pdf");

    await featureDataApi.uploadFile("agent-1", "rec-1", "license", file, "   ");

    const [, form] = requestUpload.mock.calls[0] as [string, FormData];
    expect(form.get("note")).toBeNull();
  });

  it("downloads a version through the authenticated blob helper", async () => {
    await featureDataApi.downloadFile("agent-1", "rec-1", "file/1");

    expect(requestBlob).toHaveBeenCalledWith(
      "/agents/agent-1/feature-data/records/rec-1/files/file%2F1/download",
      { cache: "no-store" },
    );
  });
});

describe("featureDataApi notifications", () => {
  it("scopes reads to the feature and can ask for unread only", async () => {
    request.mockResolvedValue({ notifications: [] });

    await featureDataApi.notifications("agent-1");
    await featureDataApi.notifications("agent-1", true);

    expect(request).toHaveBeenNthCalledWith(
      1,
      "/agents/agent-1/feature-data/notifications",
    );
    expect(request).toHaveBeenNthCalledWith(
      2,
      "/agents/agent-1/feature-data/notifications?unread_only=true",
    );
  });

  it("marks a notification read and completes its reminder", async () => {
    await featureDataApi.readNotification("agent-1", "note-1");
    await featureDataApi.completeReminder("agent-1", "rem-1");

    expect(request).toHaveBeenNthCalledWith(
      1,
      "/agents/agent-1/feature-data/notifications/note-1/read",
      { method: "POST" },
    );
    expect(request).toHaveBeenNthCalledWith(
      2,
      "/agents/agent-1/feature-data/reminders/rem-1/complete",
      { method: "POST" },
    );
  });
});
