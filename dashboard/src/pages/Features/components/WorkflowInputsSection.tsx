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

import { Button, Checkbox, Input, Select } from "antd";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
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
  const { t } = useTranslation();
  const fields = Object.entries(inputs.properties);
  const requiredNames = inputs.required ?? [];
  const sectionProblemList = sectionProblems(problems, "inputs");

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
      // The required list names fields, so it follows a rename rather than
      // quietly going on about a field that is no longer there.
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
        problems={sectionProblemList}
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

      {fields.length === 0 ? (
        <p className={styles.sectionEmpty}>
          {t("features.workflow.inputs.empty")}
        </p>
      ) : (
        <div className={styles.cardList}>
          {fields.map(([name, field], index) => (
            // Keyed by position, not by name: renaming a field rewrites the key it
            // is keyed by, which would remount the row and drop the caret mid-word.
            <div className={styles.card} key={index}>
              <div className={styles.cardHeader}>
                <span className={styles.cardIndex}>{index + 1}</span>
                <Input
                  className={styles.grow}
                  value={name}
                  disabled={readOnly}
                  aria-label={t("features.workflow.inputs.name", {
                    index: index + 1,
                  })}
                  placeholder={t("features.workflow.inputs.namePlaceholder")}
                  onChange={(event) => renameField(name, event.target.value)}
                />
                <Select
                  className={styles.typeSelect}
                  value={field.type}
                  disabled={readOnly}
                  aria-label={t("features.workflow.inputs.type", {
                    index: index + 1,
                  })}
                  options={WORKFLOW_INPUT_TYPES.map((type) => ({
                    value: type,
                    label: type,
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
                <Checkbox
                  checked={requiredNames.includes(name)}
                  disabled={readOnly}
                  onChange={(event) =>
                    toggleRequired(name, event.target.checked)
                  }
                >
                  {t("features.workflow.inputs.required")}
                </Checkbox>
                {readOnly ? null : (
                  <>
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
                  </>
                )}
              </div>

              <div className={styles.fieldRow}>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>
                    {t("features.workflow.inputs.titleZh")}
                  </span>
                  <Input
                    value={field.title.zh}
                    disabled={readOnly}
                    onChange={(event) =>
                      setField(name, {
                        title: { ...field.title, zh: event.target.value },
                      })
                    }
                  />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>
                    {t("features.workflow.inputs.titleEn")}
                  </span>
                  <Input
                    value={field.title.en}
                    disabled={readOnly}
                    onChange={(event) =>
                      setField(name, {
                        title: { ...field.title, en: event.target.value },
                      })
                    }
                  />
                </label>
              </div>

              <div className={styles.fieldRow}>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>
                    {t("features.workflow.inputs.descZh")}
                  </span>
                  <Input
                    value={field.description?.zh ?? ""}
                    disabled={readOnly}
                    onChange={(event) =>
                      setField(name, {
                        description: {
                          zh: event.target.value,
                          en: field.description?.en ?? "",
                        },
                      })
                    }
                  />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>
                    {t("features.workflow.inputs.descEn")}
                  </span>
                  <Input
                    value={field.description?.en ?? ""}
                    disabled={readOnly}
                    onChange={(event) =>
                      setField(name, {
                        description: {
                          zh: field.description?.zh ?? "",
                          en: event.target.value,
                        },
                      })
                    }
                  />
                </label>
              </div>

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
                      placeholder="text"
                      options={WORKFLOW_STRING_FORMATS.map((format) => ({
                        value: format,
                        label: format,
                      }))}
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
                      onChange={(values) => setField(name, { enum: values })}
                    />
                  </label>
                </div>
              ) : null}

              {field.type === "array" ? (
                <div className={styles.fieldRow}>
                  <label className={styles.field}>
                    <span className={styles.fieldLabel}>
                      {t("features.workflow.inputs.itemType")}
                    </span>
                    <Select
                      value={field.items?.type ?? "string"}
                      disabled={readOnly}
                      options={WORKFLOW_ITEM_TYPES.map((type) => ({
                        value: type,
                        label: type,
                      }))}
                      onChange={(type) => setField(name, { items: { type } })}
                    />
                  </label>
                </div>
              ) : null}

              {field.type === "file" ? (
                <div className={styles.fieldRow}>
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
                  <label className={styles.field}>
                    <span className={styles.fieldLabel}>
                      {t("features.workflow.inputs.multiple")}
                    </span>
                    <Checkbox
                      checked={field.multiple === true}
                      disabled={readOnly}
                      onChange={(event) =>
                        setField(name, { multiple: event.target.checked })
                      }
                    >
                      {t("features.workflow.inputs.multipleHint")}
                    </Checkbox>
                  </label>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
