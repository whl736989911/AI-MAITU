/**
 * featureData utility tests — the rendering rules every view shares.
 *
 * The load-bearing ones are the file-field exclusion (a filename must never become
 * a text value) and the required check; the rest are label resolution and the
 * schema read that decides whether the JSON editor can send at all.
 */

import { describe, expect, it } from "vitest";

import type {
  FeatureDataObject,
  FeatureDataRecord,
  FeatureDataSchema,
} from "../../../api/modules/featureData";
import {
  buildValuesPayload,
  currentFile,
  displayFieldValue,
  fieldLabel,
  filesByField,
  findObject,
  formatFileSize,
  isControlled,
  localizedText,
  missingRequiredFields,
  objectLabel,
  readSchemaJson,
  recordTitle,
  schemaObjects,
  schemaToJson,
  titleField,
  valueFields,
} from "./featureData";

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
    { key: "expires_at", label: "到期日", type: "date" },
    { key: "active", type: "boolean" },
    { key: "license", label: { zh: "资质", en: "License" }, type: "file" },
  ],
};

const SCHEMA: FeatureDataSchema = {
  version: 1,
  objects: [OBJECT],
};

function record(overrides: Partial<FeatureDataRecord> = {}): FeatureDataRecord {
  return {
    id: "rec-1",
    object_key: "supplier",
    title: "ACME",
    values: {},
    visibility: "private",
    revision: 1,
    created_by: 7,
    created_at: 100,
    updated_at: 100,
    files: [],
    ...overrides,
  };
}

describe("labels", () => {
  it("picks the reader's language and falls back to the other one", () => {
    expect(localizedText({ zh: "供应商", en: "Supplier" }, "zh")).toBe(
      "供应商",
    );
    expect(localizedText({ zh: "供应商", en: "Supplier" }, "en-US")).toBe(
      "Supplier",
    );
    expect(localizedText({ zh: "", en: "Supplier" }, "zh")).toBe("Supplier");
    expect(localizedText("plain", "zh")).toBe("plain");
    expect(localizedText(undefined, "zh", "fallback")).toBe("fallback");
  });

  it("labels objects and fields, falling back to their keys", () => {
    expect(objectLabel(OBJECT, "zh")).toBe("供应商");
    expect(fieldLabel(OBJECT.fields![1], "en")).toBe("到期日");
    expect(fieldLabel({ key: "raw", type: "text" }, "en")).toBe("raw");
  });
});

describe("file fields are never text values", () => {
  it("excludes them from the fields a value form edits", () => {
    expect(valueFields(OBJECT).map((field) => field.key)).toEqual([
      "name",
      "expires_at",
      "active",
    ]);
  });

  it("excludes them from the payload a save sends", () => {
    const payload = buildValuesPayload(OBJECT, {
      name: "ACME",
      active: true,
      license: "stray-filename.pdf",
    });

    expect(payload).toEqual({ name: "ACME", active: true });
    expect("license" in payload).toBe(false);
  });

  it("keeps a blank string, because clearing a value is an edit", () => {
    expect(buildValuesPayload(OBJECT, { name: "" })).toEqual({ name: "" });
  });

  it("drops values the object does not declare", () => {
    expect(buildValuesPayload(OBJECT, { unknown: 1, name: "ACME" })).toEqual({
      name: "ACME",
    });
  });
});

describe("required fields", () => {
  it("reports the required editable fields left blank", () => {
    expect(missingRequiredFields(OBJECT, {})).toEqual(["name"]);
    expect(missingRequiredFields(OBJECT, { name: "   " })).toEqual(["name"]);
    expect(missingRequiredFields(OBJECT, { name: "ACME" })).toEqual([]);
  });

  it("does not require a file field to have text", () => {
    // The file field is required only in the sense of an attachment; the value
    // check must not report it as a missing text answer.
    const withRequiredFile: FeatureDataObject = {
      ...OBJECT,
      fields: [
        ...(OBJECT.fields ?? []),
        { key: "doc", type: "file", required: true },
      ],
    };

    expect(missingRequiredFields(withRequiredFile, { name: "ACME" })).toEqual(
      [],
    );
  });
});

describe("values", () => {
  it("formats booleans through localized labels", () => {
    const booleanField = { key: "active", type: "boolean" as const };
    expect(displayFieldValue(booleanField, true, { yes: "是", no: "否" })).toBe(
      "是",
    );
    expect(
      displayFieldValue(booleanField, false, { yes: "是", no: "否" }),
    ).toBe("否");
  });

  it("never renders a file field even if a value sneaked in", () => {
    expect(displayFieldValue(OBJECT.fields![3], "secret.pdf")).toBe("");
  });

  it("titles a record by its title field, falling back to the id", () => {
    expect(titleField(OBJECT)).toBe("name");
    expect(recordTitle(record())).toBe("ACME");
    expect(recordTitle(record({ title: "" }))).toBe("rec-1");
  });
});

describe("schema", () => {
  it("lists only objects that carry a key", () => {
    expect(
      schemaObjects({
        objects: [{ key: "a" }, { name: "broken" } as never],
      }).map((object) => object.key),
    ).toEqual(["a"]);
    expect(findObject(SCHEMA, "supplier")).toBe(OBJECT);
    expect(findObject(SCHEMA, "missing")).toBeNull();
  });

  it("recognises a controlled document", () => {
    expect(isControlled(OBJECT)).toBe(false);
    expect(
      isControlled({ key: "doc", kind: "document", controlled: true }),
    ).toBe(true);
    expect(isControlled({ key: "doc", kind: "ledger", controlled: true })).toBe(
      false,
    );
  });

  it("reads valid JSON back into a schema", () => {
    const read = readSchemaJson(JSON.stringify(SCHEMA));
    expect(read.problem).toBeNull();
    expect(read.schema).toEqual(SCHEMA);
  });

  it("says which rule invalid editor text broke", () => {
    expect(readSchemaJson("{not json").problem).toBe("invalidJson");
    expect(readSchemaJson("[]").problem).toBe("invalidShape");
    expect(readSchemaJson('{"objects": "no"}').problem).toBe("invalidShape");
  });

  it("shows an empty document when none is stored", () => {
    expect(JSON.parse(schemaToJson(null))).toEqual({ version: 1, objects: [] });
  });
});

describe("attachments", () => {
  it("groups versions by field, newest first", () => {
    const grouped = filesByField([
      {
        id: "f1",
        field_key: "license",
        version: 1,
        filename: "a.pdf",
        content_type: "application/pdf",
        size: 10,
        sha256: "x",
        note: "",
        uploaded_by: 1,
        created_at: 1,
        current: false,
      },
      {
        id: "f2",
        field_key: "license",
        version: 2,
        filename: "a.pdf",
        content_type: "application/pdf",
        size: 12,
        sha256: "y",
        note: "renewed",
        uploaded_by: 1,
        created_at: 2,
        current: true,
      },
    ]);

    expect(grouped.license.map((file) => file.version)).toEqual([2, 1]);
    expect(currentFile(grouped.license)?.id).toBe("f2");
  });

  it("formats byte sizes", () => {
    expect(formatFileSize(0)).toBe("0 B");
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(2048)).toBe("2 KB");
  });
});
