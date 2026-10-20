/**
 * FeatureDataPanel.test.tsx — rendering, requests and saves of the data panel.
 *
 * What the panel has to prove, and why each is pinned:
 *   - the defined objects and their records are what it renders, addressed by the
 *     first object's key (the read has to be the same one a server-side feature
 *     expects);
 *   - a caller who cannot manage the feature sees no schema editor and no write
 *     buttons, while a manager's schema save sends exactly the document typed;
 *   - a record save carries the revision it read, so the server can refuse a stale
 *     write instead of overwriting a newer one;
 *   - an attachment is uploaded as a file with its note, never as a text value.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const api = vi.hoisted(() => ({
  getSchema: vi.fn(),
  putSchema: vi.fn(),
  listRecords: vi.fn(),
  createRecord: vi.fn(),
  getRecord: vi.fn(),
  updateRecord: vi.fn(),
  publishRecord: vi.fn(),
  deleteRecord: vi.fn(),
  history: vi.fn(),
  files: vi.fn(),
  uploadFile: vi.fn(),
  downloadFile: vi.fn(),
  notifications: vi.fn(),
  readNotification: vi.fn(),
  completeReminder: vi.fn(),
}));

vi.mock("../../../api/modules/featureData", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../api/modules/featureData")
  >();
  return { ...actual, featureDataApi: api };
});

vi.mock("@/utils/antdMessage", () => ({
  message: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    loading: vi.fn(() => vi.fn()),
  },
}));

vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

import { message } from "@/utils/antdMessage";

import type {
  FeatureDataObject,
  FeatureDataRecord,
  FeatureDataSchema,
  FeatureDataFile,
} from "../../../api/modules/featureData";
import FeatureDataPanel from "./FeatureDataPanel";

const OBJECT: FeatureDataObject = {
  key: "supplier",
  name: { zh: "供应商", en: "Supplier" },
  kind: "ledger",
  title_field: "name",
  fields: [
    {
      key: "name",
      label: { zh: "名称", en: "Name" },
      type: "text",
      required: true,
    },
    {
      key: "license",
      label: { zh: "资质", en: "License" },
      type: "file",
    },
  ],
};

const SCHEMA: FeatureDataSchema = { version: 1, objects: [OBJECT] };

const FILE: FeatureDataFile = {
  id: "file-1",
  field_key: "license",
  version: 1,
  filename: "license.pdf",
  content_type: "application/pdf",
  size: 2048,
  sha256: "abc",
  note: "",
  uploaded_by: 1,
  created_at: 100,
  current: true,
};

const RECORD: FeatureDataRecord = {
  id: "rec-1",
  object_key: "supplier",
  title: "ACME",
  values: { name: "ACME" },
  visibility: "private",
  revision: 1,
  created_by: 1,
  created_at: 100,
  updated_at: 100,
  files: [],
};

const userOptions = { delay: null } as const;

async function renderPanel(canWrite = true, canEditRecords = canWrite) {
  render(
    <FeatureDataPanel
      agentId="agent-1"
      canWrite={canWrite}
      canEditRecords={canEditRecords}
    />,
  );
  await screen.findByText("供应商");
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getSchema.mockResolvedValue({ schema: SCHEMA, error: null });
  api.listRecords.mockResolvedValue({ records: [RECORD], total: 1 });
  api.getRecord.mockResolvedValue({ ...RECORD, files: [FILE] });
  api.updateRecord.mockResolvedValue({
    ...RECORD,
    revision: 2,
    values: { name: "ACME 2" },
  });
  api.history.mockResolvedValue([]);
  api.notifications.mockResolvedValue([]);
  api.putSchema.mockResolvedValue({ schema: SCHEMA, error: null });
  api.createRecord.mockResolvedValue({ ...RECORD, id: "rec-2" });
  api.uploadFile.mockResolvedValue(FILE);
});

describe("<FeatureDataPanel /> reading", () => {
  it("renders the defined objects and the first object's records", async () => {
    await renderPanel();

    expect(await screen.findByText("ACME")).toBeInTheDocument();
    expect(api.listRecords).toHaveBeenCalledWith("agent-1", "supplier", {
      q: "",
      limit: 10,
      offset: 0,
    });
  });

  it("hides schema editing but lets a feature caller create records", async () => {
    await renderPanel(false, true);

    expect(
      screen.queryByText("features.data.tabSchema"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /features\.data\.newRecord/ }),
    ).toBeInTheDocument();
    // Reading still works.
    expect(await screen.findByText("ACME")).toBeInTheDocument();
  });
});

describe("<FeatureDataPanel /> schema editing", () => {
  it("edits objects and fields without showing JSON", async () => {
    const user = userEvent.setup(userOptions);
    await renderPanel();
    await user.click(screen.getByText("features.data.tabSchema"));

    expect(
      screen.queryByLabelText("features.data.schemaArea"),
    ).not.toBeInTheDocument();
    const keyInput = screen.getByLabelText("features.data.objectKey");
    await user.clear(keyInput);
    await user.type(keyInput, "supplier_form");
    expect(keyInput).toHaveValue("supplier_form");
    const nameInput = screen.getByLabelText("features.data.objectNameZh");
    await user.clear(nameInput);
    await user.type(nameInput, "供应商表单");
    await user.click(
      screen.getByRole("button", { name: "features.data.saveSchema" }),
    );

    await waitFor(() => expect(api.putSchema).toHaveBeenCalledTimes(1));
    const saved = api.putSchema.mock.calls[0][1] as FeatureDataSchema;
    expect(saved.version).toBe(1);
    expect(saved.objects?.[0].key).toBe("supplier_form");
    expect(saved.objects?.[0].name).toEqual({
      zh: "供应商表单",
      en: "Supplier",
    });
    expect(saved.objects?.[0].fields).toHaveLength(2);
    expect(message.success).toHaveBeenCalledWith("features.data.schemaSaved");
  });

  it("adds a field from the form", async () => {
    const user = userEvent.setup(userOptions);
    await renderPanel();
    await user.click(screen.getByText("features.data.tabSchema"));
    await user.click(
      screen.getByRole("button", { name: /features\.data\.addField/ }),
    );
    await user.click(
      screen.getByRole("button", { name: "features.data.saveSchema" }),
    );

    await waitFor(() => expect(api.putSchema).toHaveBeenCalledTimes(1));
    const saved = api.putSchema.mock.calls[0][1] as FeatureDataSchema;
    expect(saved.objects?.[0].fields).toHaveLength(3);
    expect(saved.objects?.[0].fields?.[2].key).toBe("field_3");
  });
});

describe("<FeatureDataPanel /> records", () => {
  it("opens a record and saves edits with the revision it read", async () => {
    const user = userEvent.setup(userOptions);
    await renderPanel();
    await user.click(await screen.findByText("ACME"));

    const nameInput = await screen.findByLabelText("名称");
    await user.clear(nameInput);
    await user.type(nameInput, "ACME 2");
    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(api.updateRecord).toHaveBeenCalledTimes(1));
    expect(api.updateRecord).toHaveBeenCalledWith(
      "agent-1",
      "rec-1",
      { name: "ACME 2" },
      1,
    );
    expect(message.success).toHaveBeenCalledWith("features.data.recordSaved");
  });

  it("creates a record from the new-record form", async () => {
    const user = userEvent.setup(userOptions);
    await renderPanel();
    await user.click(
      screen.getByRole("button", { name: /features\.data\.newRecord/ }),
    );

    const nameInput = await screen.findByLabelText("名称");
    await user.type(nameInput, "New Co");
    await user.click(screen.getByRole("button", { name: "common.create" }));

    await waitFor(() => expect(api.createRecord).toHaveBeenCalledTimes(1));
    expect(api.createRecord).toHaveBeenCalledWith("agent-1", "supplier", {
      name: "New Co",
    });
  });

  it("uploads an attachment as a file with its version note", async () => {
    const user = userEvent.setup(userOptions);
    await renderPanel();
    await user.click(await screen.findByText("ACME"));

    const note = await screen.findByLabelText("features.data.fileNote 资质");
    await user.type(note, "renewed");
    const picker = screen.getByLabelText("features.data.uploadFile 资质");
    const file = new File(["bytes"], "license.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(picker, { target: { files: [file] } });

    await waitFor(() => expect(api.uploadFile).toHaveBeenCalledTimes(1));
    const call = api.uploadFile.mock.calls[0] as [
      string,
      string,
      string,
      File,
      string,
    ];
    expect(call.slice(0, 3)).toEqual(["agent-1", "rec-1", "license"]);
    expect(call[3]).toBe(file);
    expect(call[4]).toBe("renewed");
  });
});

describe("<FeatureDataPanel /> notifications", () => {
  it("marks a notification read and completes its reminder", async () => {
    api.notifications.mockResolvedValue([
      {
        id: "note-1",
        reminder_id: "rem-1",
        title: "License expires soon",
        body: "ACME's license expires in 30 days",
        read_at: null,
        created_at: 100,
      },
    ]);
    const user = userEvent.setup(userOptions);
    await renderPanel();
    await user.click(screen.getByText("features.data.tabNotifications"));

    expect(await screen.findByText("License expires soon")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: /features\.data\.markRead/ }),
    );
    await waitFor(() =>
      expect(api.readNotification).toHaveBeenCalledWith("agent-1", "note-1"),
    );

    await user.click(
      screen.getByRole("button", { name: "features.data.completeReminder" }),
    );
    await waitFor(() =>
      expect(api.completeReminder).toHaveBeenCalledWith("agent-1", "rem-1"),
    );
  });
});
