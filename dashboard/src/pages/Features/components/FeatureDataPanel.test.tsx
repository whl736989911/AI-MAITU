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
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent, {
  PointerEventsCheckLevel,
} from "@testing-library/user-event";

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

const userOptions = {
  delay: null,
  pointerEventsCheck: PointerEventsCheckLevel.EachTarget,
} as const;

async function renderPanel(canWrite = true, canEditRecords = canWrite) {
  const { container } = render(
    <FeatureDataPanel
      agentId="agent-1"
      canWrite={canWrite}
      canEditRecords={canEditRecords}
    />,
  );
  const panelHeading = await screen.findByText("供应商");
  expect(panelHeading).toBeVisible();
  return within(container);
}

async function findRecordDialog() {
  const dialog = await screen.findByRole("dialog", { hidden: true });
  expect(dialog).toBeVisible();
  return within(dialog);
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
    const panel = await renderPanel(false, true);

    expect(
      panel.queryByText("features.data.tabSchema"),
    ).not.toBeInTheDocument();
    const newRecordButton = panel
      .getByText(/features\.data\.newRecord/)
      .closest("button")!;
    expect(newRecordButton).toBeVisible();
    expect(newRecordButton).toHaveAccessibleName(/features\.data\.newRecord/);
    // Reading still works.
    expect(await panel.findByText("ACME")).toBeInTheDocument();
  });
});

describe("<FeatureDataPanel /> schema editing", () => {
  it("edits objects and fields without showing JSON", async () => {
    const panel = await renderPanel();
    const user = userEvent.setup(userOptions);
    await user.click(panel.getByText("features.data.tabSchema"));

    expect(
      panel.queryByLabelText("features.data.schemaArea"),
    ).not.toBeInTheDocument();
    const keyInput = panel.getByLabelText("features.data.objectKey");
    await user.clear(keyInput);
    await user.paste("supplier_form");
    expect(keyInput).toHaveValue("supplier_form");
    const nameInput = panel.getByLabelText("features.data.objectNameZh");
    await user.clear(nameInput);
    await user.paste("供应商表单");
    const saveButton = panel
      .getByText("features.data.saveSchema")
      .closest("button")!;
    expect(saveButton).toBeVisible();
    expect(saveButton).toHaveAccessibleName("features.data.saveSchema");
    await user.click(saveButton);

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
    const panel = await renderPanel();
    const user = userEvent.setup(userOptions);
    await user.click(panel.getByText("features.data.tabSchema"));
    const addFieldButton = panel
      .getByText(/features\.data\.addField/)
      .closest("button")!;
    expect(addFieldButton).toBeVisible();
    expect(addFieldButton).toHaveAccessibleName(/features\.data\.addField/);
    await user.click(addFieldButton);
    const saveButton = panel
      .getByText("features.data.saveSchema")
      .closest("button")!;
    expect(saveButton).toBeVisible();
    expect(saveButton).toHaveAccessibleName("features.data.saveSchema");
    await user.click(saveButton);

    await waitFor(() => expect(api.putSchema).toHaveBeenCalledTimes(1));
    const saved = api.putSchema.mock.calls[0][1] as FeatureDataSchema;
    expect(saved.objects?.[0].fields).toHaveLength(3);
  });
});

describe("<FeatureDataPanel /> records", () => {
  it("opens a record and saves edits with the revision it read", async () => {
    const panel = await renderPanel();
    fireEvent.click(await panel.findByText("ACME"));

    const nameInput = await screen.findByLabelText("名称");
    fireEvent.change(nameInput, { target: { value: "ACME 2" } });
    const saveButton = panel.getByText("common.save").closest("button")!;
    expect(saveButton).toBeVisible();
    expect(saveButton).toHaveAccessibleName("common.save");
    fireEvent.click(saveButton);

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
    const panel = await renderPanel();
    const user = userEvent.setup(userOptions);
    const newRecordButton = panel
      .getByText(/features\.data\.newRecord/)
      .closest("button")!;
    expect(newRecordButton).toBeVisible();
    expect(newRecordButton).toHaveAccessibleName(/features\.data\.newRecord/);
    await user.click(newRecordButton);

    const nameInput = await screen.findByLabelText("名称");
    await user.click(nameInput);
    await user.paste("New Co");
    const dialog = await findRecordDialog();
    const createButton = dialog.getByText("common.create").closest("button")!;
    expect(createButton).toBeVisible();
    expect(createButton).toHaveAccessibleName("common.create");
    await user.click(createButton);

    await waitFor(() => expect(api.createRecord).toHaveBeenCalledTimes(1));
    expect(api.createRecord).toHaveBeenCalledWith("agent-1", "supplier", {
      name: "New Co",
    });
  });

  it("uploads an attachment as a file with its version note", async () => {
    const panel = await renderPanel();
    const user = userEvent.setup(userOptions);
    await user.click(await panel.findByText("ACME"));
    const note = await panel.findByLabelText("features.data.fileNote 资质");
    fireEvent.change(note, { target: { value: "renewed" } });
    const picker = panel.getByLabelText("features.data.uploadFile 资质");
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
    const panel = await renderPanel();
    fireEvent.click(panel.getByText("features.data.tabNotifications"));

    expect(await panel.findByText("License expires soon")).toBeInTheDocument();
    const markReadButton = panel
      .getByText(/features\.data\.markRead/)
      .closest("button")!;
    expect(markReadButton).toBeVisible();
    expect(markReadButton).toHaveAccessibleName(/features\.data\.markRead/);
    fireEvent.click(markReadButton);
    await waitFor(() =>
      expect(api.readNotification).toHaveBeenCalledWith("agent-1", "note-1"),
    );

    const completeButton = panel
      .getByText("features.data.completeReminder")
      .closest("button")!;
    expect(completeButton).toBeVisible();
    expect(completeButton).toHaveAccessibleName(
      "features.data.completeReminder",
    );
    fireEvent.click(completeButton);
    await waitFor(() =>
      expect(api.completeReminder).toHaveBeenCalledWith("agent-1", "rem-1"),
    );
  });
});
