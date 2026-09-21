/**
 * Schema-driven run form for a feature.
 *
 * Every control comes from the feature's own ``input_schema`` (plus the
 * ``ui_schema`` order/widget overrides) — no field is hardcoded here, so a new
 * ``feature.json`` shows up in the UI without touching this file.
 *
 * Supported widgets:
 *   string                → Input (``format: "email"`` switches the input type)
 *   string + enum         → Select
 *   string + date         → DatePicker (value stays a ``YYYY-MM-DD`` string)
 *   textarea format/widget→ Input.TextArea
 *   number / integer      → InputNumber (integer pins precision to 0)
 *   boolean               → Switch
 *   array                 → repeatable rows; arrays of arrays render as a grid
 *                           of cells with per-row add/remove
 */

import { Button, DatePicker, Input, InputNumber, Select, Switch } from "antd";
import dayjs from "dayjs";
import { Plus, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  FeatureFieldSchema,
  FeatureInputSchema,
  FeatureInputs,
  FeatureUiSchema,
  LocalizedText,
} from "../../../api/modules/features";
import { normalizeUiLocale, type UiLocale } from "../../../utils/localePrefs";
import styles from "../index.module.less";

export interface SchemaFormProps {
  schema: FeatureInputSchema;
  uiSchema?: FeatureUiSchema;
  value: FeatureInputs;
  onChange: (value: FeatureInputs) => void;
  disabled?: boolean;
}

interface ControlProps {
  schema: FeatureFieldSchema;
  /** ``ui_schema.widgets[field]`` override, e.g. ``"textarea"``. */
  widget?: string;
  value: unknown;
  disabled?: boolean;
  onChange: (value: unknown) => void;
}

/** Bilingual copy in the active UI language, falling back to the other one. */
export function localizedText(
  text: LocalizedText | undefined,
  lang: UiLocale,
): string {
  if (!text) return "";
  const preferred = lang === "zh" ? text.zh : text.en;
  return preferred || text.zh || text.en || "";
}

/** Human label for a field: its bilingual title, else the raw property name. */
export function fieldLabel(
  name: string,
  schema: FeatureFieldSchema | undefined,
  lang: UiLocale,
): string {
  return localizedText(schema?.title, lang) || name;
}

/** A field counts as blank when it was never filled in or only holds empties. */
function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.every((item) => isBlank(item));
  return false;
}

/** Required property names still missing a value (declaration order). */
export function missingRequiredFields(
  schema: FeatureInputSchema,
  value: FeatureInputs,
): string[] {
  return (schema.required ?? []).filter((name) => isBlank(value[name]));
}

/** Drop blank values so the backend only receives what the user filled in. */
export function pruneBlankInputs(value: FeatureInputs): FeatureInputs {
  const filled: FeatureInputs = {};
  for (const [name, item] of Object.entries(value)) {
    if (!isBlank(item)) filled[name] = item;
  }
  return filled;
}

/** Empty value for a newly added row/cell of the given schema. */
function emptyValue(schema: FeatureFieldSchema | undefined): unknown {
  if (!schema) return "";
  if (schema.type === "boolean") return false;
  if (schema.type === "number" || schema.type === "integer") return null;
  if (schema.type === "array") return [];
  return "";
}

/**
 * Empty grid row: nested arrays copy the previous row's cell count so a table
 * keeps its shape, and fall back to a single cell for the first row.
 */
function emptyRow(items: FeatureFieldSchema | undefined, previous: unknown): unknown {
  if (items?.type !== "array") return emptyValue(items);
  const width = Array.isArray(previous) && previous.length > 0 ? previous.length : 1;
  return Array.from({ length: width }, () => emptyValue(items.items));
}

/** ``ui_schema.order`` first, then any declared property the order omitted. */
function orderedFieldNames(
  schema: FeatureInputSchema,
  uiSchema?: FeatureUiSchema,
): string[] {
  const names: string[] = [];
  for (const name of uiSchema?.order ?? []) {
    if (name in schema.properties && !names.includes(name)) names.push(name);
  }
  for (const name of Object.keys(schema.properties)) {
    if (!names.includes(name)) names.push(name);
  }
  return names;
}

function ScalarControl({ schema, widget, value, disabled, onChange }: ControlProps) {
  const { t } = useTranslation();
  const text = value === null || value === undefined ? "" : String(value);

  if (schema.enum && schema.enum.length > 0) {
    return (
      <Select
        className={styles.control}
        placeholder={t("features.selectPlaceholder")}
        value={typeof value === "string" && value !== "" ? value : undefined}
        disabled={disabled}
        onChange={(next) => onChange(next)}
        options={schema.enum.map((item) => ({ value: item, label: item }))}
      />
    );
  }

  if (schema.format === "date") {
    return (
      <DatePicker
        className={styles.control}
        value={typeof value === "string" && value !== "" ? dayjs(value) : null}
        disabled={disabled}
        onChange={(next) => onChange(next ? next.format("YYYY-MM-DD") : "")}
      />
    );
  }

  if (schema.type === "boolean") {
    return (
      <Switch
        checked={value === true}
        disabled={disabled}
        onChange={(next) => onChange(next)}
      />
    );
  }

  if (schema.type === "number" || schema.type === "integer") {
    return (
      <InputNumber
        className={styles.control}
        value={typeof value === "number" ? value : null}
        precision={schema.type === "integer" ? 0 : undefined}
        disabled={disabled}
        onChange={(next) => onChange(next)}
      />
    );
  }

  if (widget === "textarea" || schema.format === "textarea") {
    return (
      <Input.TextArea
        className={styles.control}
        value={text}
        autoSize={{ minRows: 4, maxRows: 16 }}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }

  return (
    <Input
      className={styles.control}
      value={text}
      type={schema.format === "email" ? "email" : "text"}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

/** One row of a nested (grid) array: a horizontal run of string cells. */
function GridRow({
  itemSchema,
  value,
  disabled,
  onChange,
}: {
  itemSchema: FeatureFieldSchema;
  value: unknown;
  disabled?: boolean;
  onChange: (value: unknown) => void;
}) {
  const { t } = useTranslation();
  const cells = Array.isArray(value) ? value : [];
  const cellSchema = itemSchema.items ?? { type: "string" };

  return (
    <>
      {cells.map((cell, index) => (
        <div className={styles.gridCell} key={index}>
          <ScalarControl
            schema={cellSchema}
            value={cell}
            disabled={disabled}
            onChange={(next) =>
              onChange(cells.map((item, i) => (i === index ? next : item)))
            }
          />
          <Button
            type="text"
            size="small"
            danger
            disabled={disabled}
            title={t("features.removeCell")}
            aria-label={t("features.removeCell")}
            icon={<X size={12} />}
            onClick={() => onChange(cells.filter((_, i) => i !== index))}
          />
        </div>
      ))}
      <Button
        type="text"
        size="small"
        disabled={disabled}
        title={t("features.addCell")}
        aria-label={t("features.addCell")}
        icon={<Plus size={12} />}
        onClick={() => onChange([...cells, emptyValue(cellSchema)])}
      />
    </>
  );
}

function ArrayControl({ schema, value, disabled, onChange }: ControlProps) {
  const { t } = useTranslation();
  const items = schema.items;
  const rows = Array.isArray(value) ? value : [];
  const grid = items?.type === "array";

  const updateRow = (index: number, next: unknown) =>
    onChange(rows.map((row, i) => (i === index ? next : row)));

  return (
    <div className={styles.arrayField}>
      {rows.length === 0 && (
        <div className={styles.arrayEmpty}>{t("features.arrayEmpty")}</div>
      )}
      {rows.map((row, index) => (
        <div className={styles.arrayRow} key={index}>
          <span className={styles.arrayRowIndex}>{index + 1}</span>
          <div className={grid ? styles.arrayGrid : styles.arrayRowBody}>
            {grid ? (
              <GridRow
                itemSchema={items ?? { type: "string" }}
                value={row}
                disabled={disabled}
                onChange={(next) => updateRow(index, next)}
              />
            ) : (
              <ScalarControl
                schema={items ?? { type: "string" }}
                value={row}
                disabled={disabled}
                onChange={(next) => updateRow(index, next)}
              />
            )}
          </div>
          <Button
            type="text"
            size="small"
            danger
            disabled={disabled}
            title={t("features.removeRow")}
            aria-label={t("features.removeRow")}
            icon={<X size={14} />}
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
          />
        </div>
      ))}
      <Button
        type="dashed"
        size="small"
        block
        disabled={disabled}
        icon={<Plus size={14} />}
        onClick={() => onChange([...rows, emptyRow(items, rows[rows.length - 1])])}
      >
        {t("features.addRow")}
      </Button>
    </div>
  );
}

function ValueControl(props: ControlProps) {
  if (props.schema.type === "array") return <ArrayControl {...props} />;
  return <ScalarControl {...props} />;
}

export default function SchemaForm({
  schema,
  uiSchema,
  value,
  onChange,
  disabled,
}: SchemaFormProps) {
  const { i18n } = useTranslation();
  const lang = normalizeUiLocale(i18n.language);
  const required = schema.required ?? [];

  return (
    <div className={styles.form}>
      {orderedFieldNames(schema, uiSchema).map((name) => {
        const field = schema.properties[name];
        if (!field) return null;
        const hint = localizedText(field.description, lang);
        return (
          <div className={styles.field} key={name}>
            <div className={styles.fieldLabel}>
              {fieldLabel(name, field, lang)}
              {required.includes(name) && (
                <span className={styles.requiredMark} aria-hidden="true">
                  *
                </span>
              )}
            </div>
            {hint && <div className={styles.fieldHint}>{hint}</div>}
            <ValueControl
              schema={field}
              widget={uiSchema?.widgets?.[name]}
              value={value[name]}
              disabled={disabled}
              onChange={(next) => onChange({ ...value, [name]: next })}
            />
          </div>
        );
      })}
    </div>
  );
}
