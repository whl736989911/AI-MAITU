/**
 * One node of the input-schema editor: the controls a *property* gets at the top
 * level, plus the same set one level down for an array's element and, below
 * that, for a grid cell.
 *
 * It edits plain data through ``value`` / ``onChange`` — antd's ``Form.Item``
 * wires only the outermost node — so nesting stays structural instead of turning
 * into a pile of name paths, and no part of a schema is ever typed as text.
 */

import { Input, Select } from "antd";
import { useTranslation } from "react-i18next";
import type { FeatureFieldSchema, FeatureFieldType } from "../../../api/modules/features";
import {
  emptySchemaNode,
  FEATURE_FIELD_FORMATS,
  fieldTypesForDepth,
  normalizeSchema,
  schemaForType,
  type FeatureFieldFormat,
} from "./featureSettings";
import styles from "../index.module.less";

export const FIELD_TYPE_LABEL_KEYS: Record<FeatureFieldType, string> = {
  string: "features.settingsTypeString",
  number: "features.settingsTypeNumber",
  integer: "features.settingsTypeInteger",
  boolean: "features.settingsTypeBoolean",
  array: "features.settingsTypeArray",
};

const FORMAT_LABEL_KEYS: Record<FeatureFieldFormat, string> = {
  textarea: "features.settingsFormatTextarea",
  date: "features.settingsFormatDate",
  email: "features.settingsFormatEmail",
};

/** Caption for a nested node — the level is what tells the two selects apart. */
const LEVEL_LABEL_KEYS: Record<number, string> = {
  1: "features.settingsFieldElement",
  2: "features.settingsFieldCell",
};

export interface SchemaNodeEditorProps {
  value?: FeatureFieldSchema;
  onChange?: (value: FeatureFieldSchema) => void;
  /** 0 for a property, 1 for an array's element, 2 for a grid cell. */
  depth: number;
}

export default function SchemaNodeEditor({
  value,
  onChange,
  depth,
}: SchemaNodeEditorProps) {
  const { t } = useTranslation();
  const schema = value ?? emptySchemaNode();
  const levelKey = LEVEL_LABEL_KEYS[depth];
  const isString = schema.type === "string";

  /**
   * Every edit goes through the normalizer: switching a type has to drop the
   * keys the new type cannot carry (``format``/``enum`` are string-only,
   * ``items`` is array-only) or the payload keeps dead options the catalog
   * rejects.
   */
  const update = (patch: Partial<FeatureFieldSchema>) =>
    onChange?.(normalizeSchema({ ...schema, ...patch }));

  return (
    <div className={styles.schemaNode}>
      {levelKey && <div className={styles.schemaLevel}>{t(levelKey)}</div>}
      <div className={styles.schemaGrid}>
        <label className={styles.schemaControl}>
          <span className={styles.fieldHint}>
            {t("features.settingsFieldType")}
          </span>
          <Select
            className={styles.control}
            value={schema.type}
            options={fieldTypesForDepth(depth).map((type) => ({
              value: type,
              label: t(FIELD_TYPE_LABEL_KEYS[type]),
            }))}
            onChange={(type: FeatureFieldType) =>
              onChange?.(schemaForType(schema, type))
            }
          />
        </label>

        {isString && (
          <label className={styles.schemaControl}>
            <span className={styles.fieldHint}>
              {t("features.settingsFieldFormat")}
            </span>
            <Select
              className={styles.control}
              value={schema.format ?? ""}
              options={[
                { value: "", label: t("features.settingsFormatNone") },
                ...FEATURE_FIELD_FORMATS.map((format) => ({
                  value: format,
                  label: t(FORMAT_LABEL_KEYS[format]),
                })),
              ]}
              onChange={(format: string) => update({ format })}
            />
          </label>
        )}

        {isString && (
          <label className={`${styles.schemaControl} ${styles.schemaControlWide}`}>
            <span className={styles.fieldHint}>
              {t("features.settingsFieldEnum")}
            </span>
            <Select
              className={styles.control}
              mode="tags"
              tokenSeparators={[","]}
              value={schema.enum ?? []}
              placeholder={t("features.settingsFieldEnumPlaceholder")}
              onChange={(values: string[]) => update({ enum: values })}
            />
          </label>
        )}

        {depth === 0 && (
          <>
            <div className={`${styles.schemaControl} ${styles.schemaControlWide}`}>
              <span className={styles.fieldHint}>
                {t("features.settingsFieldCopyHint")}
              </span>
            </div>
            <label className={styles.schemaControl}>
              <span className={styles.fieldHint}>
                {t("features.settingsFieldTitleZh")}
              </span>
              <Input
                value={schema.title?.zh ?? ""}
                onChange={(event) =>
                  update({
                    title: { zh: event.target.value, en: schema.title?.en ?? "" },
                  })
                }
              />
            </label>
            <label className={styles.schemaControl}>
              <span className={styles.fieldHint}>
                {t("features.settingsFieldTitleEn")}
              </span>
              <Input
                value={schema.title?.en ?? ""}
                onChange={(event) =>
                  update({
                    title: { zh: schema.title?.zh ?? "", en: event.target.value },
                  })
                }
              />
            </label>
            <label className={styles.schemaControl}>
              <span className={styles.fieldHint}>
                {t("features.settingsFieldDescZh")}
              </span>
              <Input
                value={schema.description?.zh ?? ""}
                onChange={(event) =>
                  update({
                    description: {
                      zh: event.target.value,
                      en: schema.description?.en ?? "",
                    },
                  })
                }
              />
            </label>
            <label className={styles.schemaControl}>
              <span className={styles.fieldHint}>
                {t("features.settingsFieldDescEn")}
              </span>
              <Input
                value={schema.description?.en ?? ""}
                onChange={(event) =>
                  update({
                    description: {
                      zh: schema.description?.zh ?? "",
                      en: event.target.value,
                    },
                  })
                }
              />
            </label>
          </>
        )}

        {schema.type === "array" && (
          <div className={`${styles.schemaControl} ${styles.schemaControlWide}`}>
            <SchemaNodeEditor
              value={schema.items}
              depth={depth + 1}
              onChange={(items) => update({ items })}
            />
          </div>
        )}
      </div>
    </div>
  );
}
