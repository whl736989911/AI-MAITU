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
  listPrincipals: vi.fn(),
  listRecordReferenceOptions: vi.fn(),
  listFeatureAgents: vi.fn(),
  handover: vi.fn(),
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

const knowledgeApi = vi.hoisted(() => ({
  list: vi.fn(),
  listDocuments: vi.fn(),
  fetchDocumentFile: vi.fn(),
}));
vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: knowledgeApi,
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
  can_write: true,
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
  knowledgeApi.list.mockResolvedValue([]);
  knowledgeApi.listDocuments.mockResolvedValue([]);
  knowledgeApi.fetchDocumentFile.mockRejectedValue(
    new Error("version changed"),
  );
  api.getSchema.mockResolvedValue({ schema: SCHEMA, error: null });
  api.listRecords.mockResolvedValue({ records: [RECORD], total: 1 });
  api.listPrincipals.mockResolvedValue({ principals: [], total: 0 });
  api.listRecordReferenceOptions.mockResolvedValue({ records: [], total: 0 });
  api.listFeatureAgents.mockResolvedValue([]);
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
  it("renders the selected bilingual department after its directory loads", async () => {
    api.getSchema.mockResolvedValue({
      schema: {
        ...SCHEMA,
        objects: [
          {
            ...OBJECT,
            fields: [
              ...OBJECT.fields!,
              {
                key: "department",
                type: "org_unit",
                label: { zh: "所属部门", en: "Department" },
              },
            ],
          },
        ],
      },
      error: null,
    });
    api.getRecord.mockResolvedValue({
      ...RECORD,
      values: { ...RECORD.values, department: "quality" },
    });
    api.listPrincipals.mockResolvedValue({
      principals: [
        {
          id: 1,
          username: "qa",
          display_name: "QA officer",
          org_unit: "quality",
          org_unit_label: { zh: "质量部", en: "Quality" },
          role: "user",
        },
      ],
      total: 1,
    });

    const panel = await renderPanel();
    const user = userEvent.setup(userOptions);
    const recordRow = (await panel.findByText("ACME")).closest("tr")!;
    await waitFor(() =>
      expect(getComputedStyle(recordRow).pointerEvents).not.toBe("none"),
    );
    await user.click(recordRow);

    expect(await panel.findByText("质量部")).toBeVisible();
    expect(panel.getByRole("textbox", { name: "名称" })).toHaveValue("ACME");
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
    expect(panel.queryByText("features.data.handover")).not.toBeInTheDocument();
    // Reading still works.
    expect(await panel.findByText("ACME")).toBeInTheDocument();
  });

  it("keeps another user's shared record read-only even when the caller can create records", async () => {
    api.getRecord.mockResolvedValue({
      ...RECORD,
      visibility: "feature",
      created_by: 2,
      can_write: false,
      files: [FILE],
    });
    const panel = await renderPanel(false, true);
    const user = userEvent.setup(userOptions);
    await user.click(await panel.findByText("ACME"));
    await waitFor(() =>
      expect(
        panel.queryByText("features.data.newRecord"),
      ).not.toBeInTheDocument(),
    );
    expect(
      panel.queryByRole("button", { name: "common.save" }),
    ).not.toBeInTheDocument();
    expect(panel.queryByText("features.data.delete")).not.toBeInTheDocument();
  });
  it("keeps the newest record filter result when older requests finish later", async () => {
    const panel = await renderPanel();
    const pending: Array<{
      value: string;
      resolve: (answer: {
        records: FeatureDataRecord[];
        total: number;
      }) => void;
    }> = [];
    const rows = [
      {
        ...RECORD,
        id: "matching-record",
        title: "锻件X-100",
        values: { name: "锻件X-100" },
      },
    ];
    api.listRecords.mockImplementation(
      (
        _agentId: string,
        _objectKey: string,
        params: { filters?: Array<{ value?: unknown }> },
      ) => {
        const value = params.filters?.[0]?.value;
        if (typeof value !== "string" || !value) {
          return Promise.resolve({ records: [RECORD], total: 1 });
        }
        return new Promise((resolve) => {
          pending.push({
            value,
            resolve: resolve as (answer: {
              records: FeatureDataRecord[];
              total: number;
            }) => void,
          });
        });
      },
    );
    const user = userEvent.setup(userOptions);
    await user.click(
      panel.getByRole("button", { name: "features.data.addFilter" }),
    );
    const filterInput = panel.getByLabelText("features.data.filterValue");
    await user.type(filterInput, "锻件X-100");

    await waitFor(() => expect(pending.at(-1)?.value).toBe("锻件X-100"));
    const newest = pending.at(-1)!;
    newest.resolve({
      records: rows.filter((record) => record.values.name === newest.value),
      total: rows.filter((record) => record.values.name === newest.value)
        .length,
    });
    expect(await panel.findByText("锻件X-100")).toBeVisible();

    for (const request of pending.slice(0, -1).reverse()) {
      const matches = rows.filter(
        (record) => record.values.name === request.value,
      );
      request.resolve({ records: matches, total: matches.length });
    }
    await waitFor(() => expect(panel.getByText("锻件X-100")).toBeVisible());
    expect(panel.queryByText("锻件X-10")).not.toBeInTheDocument();
  });

  it("does not submit an empty value from a newly added filter", async () => {
    const panel = await renderPanel();
    const user = userEvent.setup(userOptions);
    await user.click(
      panel.getByRole("button", { name: "features.data.addFilter" }),
    );

    await waitFor(() => expect(api.listRecords).toHaveBeenCalled());
    expect(
      api.listRecords.mock.calls.some(([, , params]) =>
        (params.filters ?? []).some(
          (filter: { value?: unknown }) => filter.value === "",
        ),
      ),
    ).toBe(false);
    expect(
      panel.queryByText("features.data.recordsLoadFailed"),
    ).not.toBeInTheDocument();
    expect(await panel.findByText("ACME")).toBeVisible();
  });
  it.each([
    {
      label: "pinned",
      stale: false,
      version: "sha256:pinned",
      currentVersion: "sha256:current",
      button: "features.data.openOriginal",
    },
    {
      label: "current",
      stale: true,
      version: "sha256:pinned",
      currentVersion: "sha256:current",
      button: "features.data.openCurrentOriginal",
    },
  ])(
    "requests the $label knowledge original at its stated version",
    async ({ stale, version, currentVersion, button }) => {
      api.getSchema.mockResolvedValue({
        schema: {
          ...SCHEMA,
          objects: [
            {
              ...OBJECT,
              fields: [
                ...OBJECT.fields!,
                { key: "source", type: "knowledge_ref", label: "Source" },
              ],
            },
          ],
        },
        error: null,
      });
      api.getRecord.mockResolvedValue({
        ...RECORD,
        values: {
          name: "ACME",
          source: { kb_id: "kb-1", document_id: "doc-1" },
        },
        references: {
          source: {
            kind: "knowledge_ref",
            available: true,
            stale,
            kb_id: "kb-1",
            document_id: "doc-1",
            version,
            current_version: currentVersion,
            title: "source.pdf",
            original_url: "/original",
            current_original_url: "/current",
          },
        },
      });
      const panel = await renderPanel();
      const user = userEvent.setup(userOptions);
      await user.click(await panel.findByText("ACME"));
      await user.click(await panel.findByRole("button", { name: button }));

      await waitFor(() =>
        expect(knowledgeApi.fetchDocumentFile).toHaveBeenCalledWith(
          "kb-1",
          "doc-1",
          stale ? currentVersion : version,
        ),
      );
    },
  );

  it("saves ordinary fields when a required reference has been redacted", async () => {
    api.getSchema.mockResolvedValue({
      schema: {
        ...SCHEMA,
        objects: [
          {
            ...OBJECT,
            fields: [
              ...OBJECT.fields!,
              {
                key: "source",
                type: "knowledge_ref",
                label: { zh: "原件", en: "Original" },
                required: true,
              },
            ],
          },
        ],
      },
      error: null,
    });
    const redactedRecord = {
      ...RECORD,
      values: { name: "ACME", source: { redacted: true } },
      references: { source: { kind: "knowledge_ref", available: false } },
    };
    api.getRecord.mockResolvedValue(redactedRecord);
    api.updateRecord.mockResolvedValue({
      ...redactedRecord,
      title: "已更新的记录",
      values: { ...redactedRecord.values, name: "ACME 2" },
      revision: 2,
    });
    const panel = await renderPanel(false, true);
    const user = userEvent.setup(userOptions);
    await user.click(await panel.findByText("ACME"));
    const name = await panel.findByLabelText("名称");
    await user.clear(name);
    await user.paste("ACME 2");
    await user.click(panel.getByRole("button", { name: "common.save" }));
    expect(await panel.findByText("已更新的记录")).toBeVisible();
  });
});

describe("<FeatureDataPanel /> schema editing", () => {
  it("keeps a stored fixed calendar offset visible and editable", async () => {
    api.getSchema.mockResolvedValue({
      schema: {
        ...SCHEMA,
        objects: [
          {
            ...OBJECT,
            fields: [
              ...OBJECT.fields!,
              { key: "issued", type: "date" },
              {
                key: "due",
                type: "date",
                computed: {
                  op: "add_months",
                  source_field: "issued",
                  amount: 12,
                },
              },
            ],
          },
        ],
      },
      error: null,
    });
    const panel = await renderPanel();
    const user = userEvent.setup(userOptions);
    await user.click(panel.getByText("features.data.tabSchema"));

    const amount = panel.getByRole("spinbutton", {
      name: "features.data.computedAmount",
    });
    expect(amount).toHaveValue(12);
    await user.clear(amount);
    await user.type(amount, "6");
    expect(amount).toHaveValue(6);
  });

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
