/**
 * The dynamic value form for one business object.
 *
 * A field's type decides its control; ``file`` fields have no control here at all
 * because they are managed by the attachment section. Keeping that split in the
 * component is what makes a file field impossible to type into by accident.
 *
 * Controlled by the caller: the record detail and the create modal both edit a
 * ``values`` map, so this component owns no state and no save.
 */

import { Input, InputNumber, Select, Switch } from "antd";
import { useTranslation } from "react-i18next";

import type {
  FeatureDataField,
  FeatureDataObject,
} from "../../../api/modules/featureData";
import { fieldLabel, valueFields } from "../utils/featureData";
import styles from "./FeatureDataPanel.module.less";

export interface FeatureDataRecordFieldsProps {
  object: FeatureDataObject;
  values: Record<string, unknown>;
  readOnly?: boolean;
  /** Problems given as field keys by ``missingRequiredFields``. */
  missing?: readonly string[];
  onChange?: (key: string, value: unknown) => void;
}

function textValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  return String(value);
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export default function FeatureDataRecordFields({
  object,
  values,
  readOnly = false,
  missing = [],
  onChange,
}: FeatureDataRecordFieldsProps) {
  const { t, i18n } = useTranslation();
  const language = i18n.language ?? "en";
  const fields = valueFields(object);
  if (fields.length === 0) {
    return <p className={styles.hint}>{t("features.data.noFields")}</p>;
  }
  const missingKeys = new Set(missing);

  const emit = (field: FeatureDataField, value: unknown) => {
    onChange?.(field.key, value);
  };

  const renderControl = (field: FeatureDataField) => {
    const value = values[field.key];
    const disabled = readOnly;
    const label = fieldLabel(field, language);
    switch (field.type) {
      case "textarea":
        return (
          <Input.TextArea
            aria-label={label}
            value={textValue(value)}
            disabled={disabled}
            autoSize={{ minRows: 2, maxRows: 6 }}
            onChange={(event) => emit(field, event.target.value)}
          />
        );
      case "number":
      case "integer":
        return (
          <InputNumber
            aria-label={label}
            className={styles.numberInput}
            value={numberValue(value)}
            disabled={disabled}
            precision={field.type === "integer" ? 0 : undefined}
            step={field.type === "integer" ? 1 : undefined}
            onChange={(next) => emit(field, next ?? undefined)}
          />
        );
      case "boolean":
        return (
          <Switch
            aria-label={label}
            checked={value === true}
            disabled={disabled}
            onChange={(checked) => emit(field, checked)}
          />
        );
      case "date":
        return (
          <Input
            aria-label={label}
            type="date"
            value={textValue(value)}
            disabled={disabled}
            onChange={(event) => emit(field, event.target.value)}
          />
        );
      case "datetime":
        return (
          <Input
            aria-label={label}
            type="datetime-local"
            value={textValue(value)}
            disabled={disabled}
            onChange={(event) => emit(field, event.target.value)}
          />
        );
      case "enum":
        return (
          <Select
            aria-label={label}
            allowClear
            value={typeof value === "string" && value ? value : undefined}
            disabled={disabled}
            placeholder={t("features.data.selectOption")}
            options={(field.options ?? []).map((option) => ({
              value: option,
              label: option,
            }))}
            onChange={(next) => emit(field, next ?? undefined)}
          />
        );
      default:
        return (
          <Input
            aria-label={label}
            value={textValue(value)}
            disabled={disabled}
            onChange={(event) => emit(field, event.target.value)}
          />
        );
    }
  };

  return (
    <div className={styles.fields}>
      {fields.map((field) => {
        const label = fieldLabel(field, language);
        const isMissing = missingKeys.has(field.key);
        return (
          <div className={styles.field} key={field.key}>
            <span className={styles.fieldLabel}>
              {label}
              {field.required ? (
                <span className={styles.requiredMark}>
                  {t("features.data.requiredMark")}
                </span>
              ) : null}
            </span>
            {renderControl(field)}
            {isMissing ? (
              <span className={styles.fieldError}>
                {t("features.data.fieldRequired", { label })}
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
