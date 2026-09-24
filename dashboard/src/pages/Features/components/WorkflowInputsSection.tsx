/**
 * 输入 — the form a run asks for before it starts.
 *
 * The fields are ``inputs.properties``, in the order they are declared, because that
 * order *is* the form's order: the card list here is the form a caller fills in, so
 * it is rendered as the document holds it rather than sorted or grouped.
 *
 * A field's ``type`` decides which of the rest apply (``format``/``enum`` for a
 * string, ``items`` for an array, ``accept``/``multiple`` for a file). The controls
 * a type does not use are hidden rather than cleared: switching a field from
 * ``file`` to ``string`` and back is a look at another type, not a decision to
 * forget what was typed — and what the type cannot use is dropped on the way to the
 * document (``normalizeWorkflow``) rather than refused.
 */

import { Button, Checkbox, Input, InputNumber, Select, Switch } from "antd";
import {
  ArrowDown,
  ArrowUp,
  Paperclip,
  Play,
  Plus,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  WORKFLOW_INPUT_TYPES,
  WORKFLOW_ITEM_TYPES,
  WORKFLOW_STRING_FORMATS,
  type WorkflowInputField,
  type WorkflowInputs,
} from "../../../api/modules/featureWorkflow";
import {
  MAX_INPUT_FIELDS,
  moveItem,
  renameInputField,
  sectionProblems,
} from "../utils/workflowDocument";
import WorkflowSectionHeader from "./WorkflowSectionHeader";
import chatStyles from "../../Chat/components/WorkflowInputCard.module.less";
import styles from "./FeatureWorkflowPanel.module.less";

export interface WorkflowInputsSectionProps {
  /** The declared form; the panel supplies an empty one for a new definition. */
  inputs: WorkflowInputs;
  /** Every current problem — this section keeps the ones that are its own. */
  problems: readonly string[];
  readOnly: boolean;
  onChange: (inputs: WorkflowInputs) => void;
}

/** A field name no other field holds: a field *is* a key, so it has to have one. */
function nextFieldName(used: ReadonlySet<string>): string {
  let index = used.size + 1;
  while (used.has(`field_${index}`)) index += 1;
  return `field_${index}`;
}

export default function WorkflowInputsSection({
  inputs,
  problems,
  readOnly,
  onChange,
}: WorkflowInputsSectionProps) {
  const { t, i18n } = useTranslation();
  const language = i18n.language?.startsWith("zh") ? "zh" : "en";
  const otherLanguage = language === "zh" ? "en" : "zh";
  const fields = Object.entries(inputs.properties);
  const requiredNames = inputs.required ?? [];

  const setField = (name: string, patch: Partial<WorkflowInputField>) => {
    const current = inputs.properties[name];
    if (!current) return;
    onChange({
      ...inputs,
      properties: { ...inputs.properties, [name]: { ...current, ...patch } },
    });
  };

  const renameField = (from: string, to: string) => {
    onChange({
      ...inputs,
      properties: renameInputField(inputs.properties, from, to),
      required: requiredNames.map((name) => (name === from ? to : name)),
    });
  };

  const toggleRequired = (name: string, checked: boolean) => {
    onChange({
      ...inputs,
      required: checked
        ? [...requiredNames, name]
        : requiredNames.filter((item) => item !== name),
    });
  };

  const removeField = (name: string) => {
    onChange({
      ...inputs,
      properties: Object.fromEntries(
        Object.entries(inputs.properties).filter(([key]) => key !== name),
      ),
      required: requiredNames.filter((item) => item !== name),
    });
  };

  const addField = () => {
    const name = nextFieldName(new Set(Object.keys(inputs.properties)));
    onChange({
      ...inputs,
      properties: {
        ...inputs.properties,
        [name]: { type: "string", title: { zh: "", en: "" } },
      },
    });
  };

  const moveField = (from: number, to: number) => {
    onChange({
      ...inputs,
      properties: Object.fromEntries(moveItem(fields, from, to)),
    });
  };

  return (
    <section className={styles.section}>
      <WorkflowSectionHeader
        title={t("features.workflow.inputs.title")}
        hint={t("features.workflow.inputs.hint")}
        problems={sectionProblems(problems, "inputs")}
        action={
          readOnly ? undefined : (
            <Button
              size="small"
              icon={<Plus size={13} />}
              disabled={fields.length >= MAX_INPUT_FIELDS}
              onClick={addField}
            >
              {t("features.workflow.inputs.add")}
            </Button>
          )
        }
      />
      <p className={styles.previewCardCaption}>
        {t("features.workflow.inputs.preview")}
      </p>
      <div className={chatStyles.card}>
        <div className={chatStyles.header}>
          <span className={chatStyles.title}>
            {t("chat.workflow.inputTitle")}
          </span>
        </div>
        {fields.length === 0 ? (
          <p className={styles.sectionEmpty}>
            {t("features.workflow.inputs.empty")}
          </p>
        ) : (
          <div className={chatStyles.fields}>
            {fields.map(([name, field], index) => {
              const required = requiredNames.includes(name);
              return (
                <div className={styles.previewField} key={index}>
                  <div className={styles.previewFieldHeader}>
                    <span className={styles.cardIndex}>{index + 1}</span>
                    <Select
                      className={styles.grow}
                      size="small"
                      value={field.type}
                      disabled={readOnly}
                      aria-label={t("features.workflow.inputs.type", {
                        index: index + 1,
                      })}
                      options={WORKFLOW_INPUT_TYPES.map((type) => ({
                        value: type,
                        label: t(
                          `features.workflow.inputs.typeOptions.${type}`,
                        ),
                      }))}
                      onChange={(type) =>
                        setField(name, {
                          type,
                          ...(type === "array"
                            ? { items: { type: field.items?.type ?? "string" } }
                            : {}),
                        })
                      }
                    />
                    {readOnly ? null : (
                      <span className={styles.previewFieldActions}>
                        <Button
                          type="text"
                          size="small"
                          icon={<ArrowUp size={13} />}
                          disabled={index === 0}
                          aria-label={t("features.workflow.moveUp", {
                            index: index + 1,
                          })}
                          onClick={() => moveField(index, index - 1)}
                        />
                        <Button
                          type="text"
                          size="small"
                          icon={<ArrowDown size={13} />}
                          disabled={index === fields.length - 1}
                          aria-label={t("features.workflow.moveDown", {
                            index: index + 1,
                          })}
                          onClick={() => moveField(index, index + 1)}
                        />
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<Trash2 size={13} />}
                          aria-label={t("features.workflow.inputs.remove", {
                            index: index + 1,
                          })}
                          onClick={() => removeField(name)}
                        />
                      </span>
                    )}
                  </div>

                  <label className={chatStyles.field}>
                    <span className={chatStyles.fieldLabel}>
                      {t(
                        language === "zh"
                          ? "features.workflow.inputs.titleZh"
                          : "features.workflow.inputs.titleEn",
                      )}
                      {required ? (
                        <span className={styles.previewRequired}> *</span>
                      ) : null}
                    </span>
                    <Input
                      value={field.title[language]}
                      disabled={readOnly}
                      placeholder={t(
                        "features.workflow.inputs.fieldPlaceholder",
                      )}
                      onChange={(event) =>
                        setField(name, {
                          title: {
                            ...field.title,
                            [language]: event.target.value,
                            [otherLanguage]:
                              field.title[otherLanguage] ===
                              field.title[language]
                                ? event.target.value
                                : field.title[otherLanguage],
                          },
                        })
                      }
                    />
                  </label>
                  <label className={chatStyles.field}>
                    <span className={chatStyles.fieldLabel}>
                      {t(
                        language === "zh"
                          ? "features.workflow.inputs.descZh"
                          : "features.workflow.inputs.descEn",
                      )}
                    </span>
                    <Input
                      value={field.description?.[language] ?? ""}
                      disabled={readOnly}
                      onChange={(event) =>
                        setField(name, {
                          description: {
                            zh: field.description?.zh ?? "",
                            en: field.description?.en ?? "",
                            [language]: event.target.value,
                            [otherLanguage]:
                              (field.description?.[otherLanguage] ?? "") ===
                              (field.description?.[language] ?? "")
                                ? event.target.value
                                : field.description?.[otherLanguage] ?? "",
                          },
                        })
                      }
                    />
                  </label>

                  <div
                    className={styles.previewFieldControl}
                    aria-hidden="true"
                  >
                    <PreviewInputControl field={field} />
                  </div>
                  <Checkbox
                    checked={required}
                    disabled={readOnly}
                    onChange={(event) =>
                      toggleRequired(name, event.target.checked)
                    }
                  >
                    {t("features.workflow.inputs.required")}
                  </Checkbox>

                  <details className={styles.previewDetails}>
                    <summary className={styles.previewDetailsSummary}>
                      {t("features.workflow.inputs.details")}
                    </summary>
                    <div className={styles.previewDetailsBody}>
                      <label className={styles.field}>
                        <span className={styles.fieldLabel}>
                          {t("features.workflow.inputs.name", {
                            index: index + 1,
                          })}
                        </span>
                        <Input
                          value={name}
                          disabled={readOnly}
                          placeholder={t(
                            "features.workflow.inputs.namePlaceholder",
                          )}
                          onChange={(event) =>
                            renameField(name, event.target.value)
                          }
                        />
                      </label>
                      <label className={styles.field}>
                        <span className={styles.fieldLabel}>
                          {t(
                            otherLanguage === "zh"
                              ? "features.workflow.inputs.titleZh"
                              : "features.workflow.inputs.titleEn",
                          )}
                        </span>
                        <Input
                          value={field.title[otherLanguage]}
                          disabled={readOnly}
                          onChange={(event) =>
                            setField(name, {
                              title: {
                                ...field.title,
                                [otherLanguage]: event.target.value,
                              },
                            })
                          }
                        />
                      </label>
                      <label className={styles.field}>
                        <span className={styles.fieldLabel}>
                          {t(
                            otherLanguage === "zh"
                              ? "features.workflow.inputs.descZh"
                              : "features.workflow.inputs.descEn",
                          )}
                        </span>
                        <Input
                          value={field.description?.[otherLanguage] ?? ""}
                          disabled={readOnly}
                          onChange={(event) =>
                            setField(name, {
                              description: {
                                zh: field.description?.zh ?? "",
                                en: field.description?.en ?? "",
                                [otherLanguage]: event.target.value,
                              },
                            })
                          }
                        />
                      </label>
                      {field.type === "string" ? (
                        <div className={styles.fieldRow}>
                          <label className={styles.field}>
                            <span className={styles.fieldLabel}>
                              {t("features.workflow.inputs.format")}
                            </span>
                            <Select
                              allowClear
                              value={field.format}
                              disabled={readOnly}
                              options={WORKFLOW_STRING_FORMATS.map(
                                (format) => ({
                                  value: format,
                                  label: t(
                                    `features.workflow.inputs.formatOptions.${format}`,
                                  ),
                                }),
                              )}
                              onChange={(format) => setField(name, { format })}
                            />
                          </label>
                          <label className={styles.field}>
                            <span className={styles.fieldLabel}>
                              {t("features.workflow.inputs.enum")}
                            </span>
                            <Select
                              mode="tags"
                              value={field.enum ?? []}
                              disabled={readOnly}
                              tokenSeparators={[","]}
                              placeholder={t(
                                "features.workflow.inputs.enumPlaceholder",
                              )}
                              onChange={(values) =>
                                setField(name, { enum: values })
                              }
                            />
                          </label>
                        </div>
                      ) : null}
                      {field.type === "array" ? (
                        <label className={styles.field}>
                          <span className={styles.fieldLabel}>
                            {t("features.workflow.inputs.itemType")}
                          </span>
                          <Select
                            value={field.items?.type ?? "string"}
                            disabled={readOnly}
                            options={WORKFLOW_ITEM_TYPES.map((type) => ({
                              value: type,
                              label: t(
                                `features.workflow.inputs.itemTypeOptions.${type}`,
                              ),
                            }))}
                            onChange={(type) =>
                              setField(name, { items: { type } })
                            }
                          />
                        </label>
                      ) : null}
                      {field.type === "file" ? (
                        <>
                          <label className={styles.field}>
                            <span className={styles.fieldLabel}>
                              {t("features.workflow.inputs.accept")}
                            </span>
                            <Input
                              value={field.accept ?? ""}
                              disabled={readOnly}
                              placeholder=".pdf,.docx"
                              onChange={(event) =>
                                setField(name, { accept: event.target.value })
                              }
                            />
                          </label>
                          <Checkbox
                            checked={field.multiple === true}
                            disabled={readOnly}
                            onChange={(event) =>
                              setField(name, { multiple: event.target.checked })
                            }
                          >
                            {t("features.workflow.inputs.multipleHint")}
                          </Checkbox>
                        </>
                      ) : null}
                    </div>
                  </details>
                </div>
              );
            })}
          </div>
        )}
        {fields.length > 0 ? (
          <div className={chatStyles.actions}>
            <Button type="primary" icon={<Play size={13} />} disabled>
              {t("chat.workflow.run")}
            </Button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

/** An inert preview: author edits the question, never submits a run here. */
function PreviewInputControl({ field }: { field: WorkflowInputField }) {
  const { t } = useTranslation();
  if (field.type === "file") {
    return (
      <Button size="small" icon={<Paperclip size={13} />} disabled>
        {t("chat.workflow.chooseFile")}
      </Button>
    );
  }
  if (field.type === "boolean") return <Switch disabled />;
  if (field.type === "number" || field.type === "integer") {
    return <InputNumber className={chatStyles.numberInput} disabled />;
  }
  if (field.type === "array") {
    return (
      <Select
        mode="tags"
        className={chatStyles.wide}
        disabled
        placeholder={t("chat.workflow.arrayPlaceholder")}
      />
    );
  }
  if (field.enum?.length) {
    return (
      <Select
        className={chatStyles.wide}
        disabled
        placeholder={t("chat.workflow.selectPlaceholder")}
        options={field.enum.map((option) => ({
          value: option,
          label: option,
        }))}
      />
    );
  }
  if (field.format === "textarea") {
    return <Input.TextArea disabled autoSize={{ minRows: 2, maxRows: 2 }} />;
  }
  return (
    <Input
      disabled
      type={
        field.format === "email"
          ? "email"
          : field.format === "date"
          ? "date"
          : "text"
      }
    />
  );
}
