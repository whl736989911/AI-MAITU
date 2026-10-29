/**
 * Form-based business-object designer.
 *
 * Most feature managers should never see JSON. This editor exposes the same
 * document as nested forms (objects → fields); the server still validates the
 * resulting document and returns every problem at once. A JSON editor is not a
 * prerequisite for using feature data.
 */

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Input,
  Select,
  Space,
  Spin,
  Tag,
} from "antd";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { message } from "@/utils/antdMessage";

import {
  FEATURE_DATA_FIELD_TYPES,
  type FeatureDataField,
  type FeatureDataFieldType,
  type FeatureDataObject,
  type FeatureDataSchema,
} from "../../../api/modules/featureData";
import { featureDataApi } from "../../../api/modules/featureData";
import { apiErrorMessage, parseApiError } from "../../../utils/apiError";
import { emptySchema } from "../utils/featureData";
import styles from "./FeatureDataPanel.module.less";

export interface FeatureDataSchemaEditorProps {
  agentId: string;
  schema: FeatureDataSchema | null;
  /** The stored schema could not be read; the editor starts from an empty one. */
  storedError: string | null;
  onSaved: (schema: FeatureDataSchema) => void;
}

function nameOf(value: FeatureDataObject["name"] | FeatureDataField["label"]): {
  zh: string;
  en: string;
} {
  if (typeof value === "string") return { zh: value, en: value };
  return { zh: value?.zh ?? "", en: value?.en ?? "" };
}

function emptyField(index: number): FeatureDataField {
  return {
    key: `field_${index}`,
    label: { zh: `字段 ${index}`, en: `Field ${index}` },
    type: "text",
  };
}

function emptyObject(index: number): FeatureDataObject {
  return {
    key: `object_${index}`,
    name: { zh: `业务对象 ${index}`, en: `Object ${index}` },
    kind: "ledger",
    controlled: false,
    title_field: "",
    fields: [emptyField(1)],
  };
}

const FEATURE_ROLE_KEYS = [
  "admin",
  "enterprise_admin",
  "unit_admin",
  "user",
] as const;
function parseCsv(value: string): string[] {
  return value
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export default function FeatureDataSchemaEditor({
  agentId,
  schema,
  storedError,
  onSaved,
}: FeatureDataSchemaEditorProps) {
  const { t } = useTranslation();
  const [objects, setObjects] = useState<FeatureDataObject[]>(() =>
    schema?.objects?.length
      ? schema.objects.map((item) => ({ ...item }))
      : [emptyObject(1)],
  );
  const [saving, setSaving] = useState(false);
  const [serverProblems, setServerProblems] = useState<readonly string[]>([]);
  const [featureAgents, setFeatureAgents] = useState<
    { agent_id: string; kind?: string | null; name: string }[]
  >([]);
  const [referencedObjects, setReferencedObjects] = useState<
    Record<string, FeatureDataObject[]>
  >({});

  useEffect(() => {
    setObjects(
      schema?.objects?.length
        ? schema.objects.map((item) => ({ ...item }))
        : [emptyObject(1)],
    );
    setServerProblems([]);
  }, [schema]);
  useEffect(() => {
    void featureDataApi
      .listFeatureAgents()
      .then((rows) =>
        setFeatureAgents(rows.filter((row) => row.kind === "feature")),
      )
      .catch(() => setFeatureAgents([]));
  }, []);

  const patchObject = (index: number, patch: Partial<FeatureDataObject>) => {
    setObjects((current) =>
      current.map((item, at) => (at === index ? { ...item, ...patch } : item)),
    );
  };

  const patchField = (
    objectIndex: number,
    fieldIndex: number,
    patch: Partial<FeatureDataField>,
  ) => {
    setObjects((current) =>
      current.map((item, at) => {
        if (at !== objectIndex) return item;
        return {
          ...item,
          fields: (item.fields ?? []).map((field, fieldAt) =>
            fieldAt === fieldIndex ? { ...field, ...patch } : field,
          ),
        };
      }),
    );
  };

  const save = async () => {
    const document: FeatureDataSchema = {
      ...schema,
      version: schema?.version ?? 1,
      objects: objects.map((item) => ({
        ...item,
        title_field: item.title_field || item.fields?.[0]?.key || "",
        fields: (item.fields ?? []).map((field) => ({
          ...field,
          required: Boolean(field.required),
        })),
      })),
    };
    setSaving(true);
    setServerProblems([]);
    try {
      const answer = await featureDataApi.putSchema(agentId, document);
      onSaved(answer.schema ?? document ?? emptySchema());
      message.success(t("features.data.schemaSaved"));
    } catch (error) {
      const parsed = parseApiError(error);
      const reason = parsed?.details?.reason;
      if (typeof reason === "string" && reason.trim()) {
        setServerProblems(
          reason
            .split("; ")
            .map((item) => item.trim())
            .filter(Boolean),
        );
      } else {
        message.error(
          apiErrorMessage(error, t("features.data.schemaSaveFailed"), t),
        );
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={styles.schemaPane}>
      {storedError === null ? null : (
        <Alert
          type="warning"
          showIcon
          className={styles.notice}
          message={t("features.data.schemaStoredError")}
          description={storedError}
        />
      )}
      <p className={styles.hint}>{t("features.data.schemaFormIntro")}</p>
      {serverProblems.length > 0 ? (
        <Alert
          type="error"
          showIcon
          className={styles.notice}
          message={t("features.data.schemaRefused")}
          description={
            <ul className={styles.problemList}>
              {serverProblems.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          }
        />
      ) : null}

      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {objects.map((object, objectIndex) => {
          const objectName = nameOf(object.name);
          const fields = object.fields ?? [];
          return (
            <Card
              key={`object-${objectIndex}`}
              size="small"
              title={
                <Space>
                  <Tag color={object.kind === "document" ? "blue" : "green"}>
                    {object.kind === "document"
                      ? t("features.data.kindDocument")
                      : t("features.data.kindLedger")}
                  </Tag>
                  <span>{objectName.zh || object.key}</span>
                </Space>
              }
              extra={
                objects.length > 1 ? (
                  <Button
                    type="text"
                    danger
                    size="small"
                    icon={<Trash2 size={14} />}
                    onClick={() =>
                      setObjects((current) =>
                        current.filter((_, at) => at !== objectIndex),
                      )
                    }
                  >
                    {t("features.data.removeObject")}
                  </Button>
                ) : null
              }
            >
              <Space wrap size="small" style={{ width: "100%" }}>
                <Input
                  aria-label={t("features.data.objectKey")}
                  addonBefore={t("features.data.objectKey")}
                  value={object.key}
                  onChange={(event) =>
                    patchObject(objectIndex, { key: event.target.value })
                  }
                  style={{ width: 250 }}
                />
                <Input
                  aria-label={t("features.data.objectNameZh")}
                  addonBefore={t("features.data.objectNameZh")}
                  value={objectName.zh}
                  onChange={(event) =>
                    patchObject(objectIndex, {
                      name: { ...objectName, zh: event.target.value },
                    })
                  }
                  style={{ width: 260 }}
                />
                <Input
                  aria-label={t("features.data.objectNameEn")}
                  addonBefore={t("features.data.objectNameEn")}
                  value={objectName.en}
                  onChange={(event) =>
                    patchObject(objectIndex, {
                      name: { ...objectName, en: event.target.value },
                    })
                  }
                  style={{ width: 260 }}
                />
                <Select
                  aria-label={t("features.data.objectKind")}
                  value={object.kind ?? "ledger"}
                  onChange={(kind) =>
                    patchObject(objectIndex, {
                      kind,
                      controlled: kind === "document" && object.controlled,
                    })
                  }
                  options={[
                    {
                      value: "ledger",
                      label: t("features.data.kindLedger"),
                    },
                    {
                      value: "document",
                      label: t("features.data.kindDocument"),
                    },
                  ]}
                  style={{ width: 150 }}
                />
                <Checkbox
                  checked={Boolean(object.controlled)}
                  disabled={object.kind !== "document"}
                  onChange={(event) =>
                    patchObject(objectIndex, {
                      controlled: event.target.checked,
                    })
                  }
                >
                  {t("features.data.controlled")}
                </Checkbox>
                <Select
                  value={object.title_field || undefined}
                  placeholder={t("features.data.titleField")}
                  onChange={(title_field) =>
                    patchObject(objectIndex, { title_field })
                  }
                  options={fields.map((field) => ({
                    value: field.key,
                    label: nameOf(field.label).zh || field.key,
                  }))}
                  style={{ width: 180 }}
                />
              </Space>
              <Space wrap size="small" style={{ marginTop: 8 }}>
                <Checkbox
                  checked={object.reminder_recipients?.creator}
                  onChange={(event) =>
                    patchObject(objectIndex, {
                      reminder_recipients: {
                        ...object.reminder_recipients,
                        creator: event.target.checked,
                      },
                    })
                  }
                >
                  {t("features.data.recipientCreator")}
                </Checkbox>
                <Checkbox
                  checked={object.reminder_recipients?.owner}
                  onChange={(event) =>
                    patchObject(objectIndex, {
                      reminder_recipients: {
                        ...object.reminder_recipients,
                        owner: event.target.checked,
                      },
                    })
                  }
                >
                  {t("features.data.recipientOwner")}
                </Checkbox>
                <Checkbox
                  checked={object.reminder_recipients?.responsible}
                  onChange={(event) =>
                    patchObject(objectIndex, {
                      reminder_recipients: {
                        ...object.reminder_recipients,
                        responsible: event.target.checked,
                      },
                    })
                  }
                >
                  {t("features.data.recipientResponsible")}
                </Checkbox>
                <Input
                  aria-label={t("features.data.recipientUserFields")}
                  placeholder={t("features.data.recipientUserFields")}
                  value={(object.reminder_recipients?.user_fields ?? []).join(
                    ", ",
                  )}
                  onChange={(event) =>
                    patchObject(objectIndex, {
                      reminder_recipients: {
                        ...object.reminder_recipients,
                        user_fields: parseCsv(event.target.value),
                      },
                    })
                  }
                />
                <Input
                  aria-label={t("features.data.recipientUnitFields")}
                  placeholder={t("features.data.recipientUnitFields")}
                  value={(object.reminder_recipients?.unit_fields ?? []).join(
                    ", ",
                  )}
                  onChange={(event) =>
                    patchObject(objectIndex, {
                      reminder_recipients: {
                        ...object.reminder_recipients,
                        unit_fields: parseCsv(event.target.value),
                      },
                    })
                  }
                />
                <Select
                  mode="multiple"
                  aria-label={t("features.data.recipientRoles")}
                  placeholder={t("features.data.recipientRoles")}
                  value={object.reminder_recipients?.role_keys ?? []}
                  options={FEATURE_ROLE_KEYS.map((role) => ({
                    value: role,
                    label: t(
                      role === "enterprise_admin"
                        ? "roles.enterpriseAdmin"
                        : role === "admin"
                        ? "account.roleAdmin"
                        : role === "unit_admin"
                        ? "account.roleUnitAdmin"
                        : "account.roleUser",
                    ),
                  }))}
                  onChange={(role_keys) =>
                    patchObject(objectIndex, {
                      reminder_recipients: {
                        ...object.reminder_recipients,
                        role_keys,
                      },
                    })
                  }
                  style={{ minWidth: 260 }}
                />
              </Space>

              <div style={{ marginTop: 12 }}>
                <Space
                  direction="vertical"
                  size="small"
                  style={{ width: "100%" }}
                >
                  {fields.map((field, fieldIndex) => {
                    const fieldLabel = nameOf(field.label);
                    const fieldType = field.type ?? "text";
                    const targetAgentId = field.target_agent_id || agentId;
                    const targetObjectOptions =
                      targetAgentId === agentId
                        ? objects
                        : referencedObjects[targetAgentId] ?? [];
                    return (
                      <Card key={`field-${fieldIndex}`} size="small">
                        <Space wrap size="small">
                          <Input
                            aria-label={t("features.data.fieldKey")}
                            addonBefore={t("features.data.fieldKey")}
                            value={field.key}
                            onChange={(event) =>
                              patchField(objectIndex, fieldIndex, {
                                key: event.target.value,
                              })
                            }
                            style={{ width: 220 }}
                          />
                          <Input
                            aria-label={t("features.data.fieldLabelZh")}
                            addonBefore={t("features.data.fieldLabelZh")}
                            value={fieldLabel.zh}
                            onChange={(event) =>
                              patchField(objectIndex, fieldIndex, {
                                label: {
                                  ...fieldLabel,
                                  zh: event.target.value,
                                },
                              })
                            }
                            style={{ width: 230 }}
                          />
                          <Input
                            aria-label={t("features.data.fieldLabelEn")}
                            addonBefore={t("features.data.fieldLabelEn")}
                            value={fieldLabel.en}
                            onChange={(event) =>
                              patchField(objectIndex, fieldIndex, {
                                label: {
                                  ...fieldLabel,
                                  en: event.target.value,
                                },
                              })
                            }
                            style={{ width: 230 }}
                          />
                          <Select<FeatureDataFieldType>
                            aria-label={t("features.data.fieldType")}
                            value={fieldType}
                            onChange={(type) =>
                              patchField(objectIndex, fieldIndex, { type })
                            }
                            options={FEATURE_DATA_FIELD_TYPES.map((type) => ({
                              value: type,
                              label: t(`features.data.fieldTypes.${type}`),
                            }))}
                            style={{ width: 150 }}
                          />
                          <Checkbox
                            checked={Boolean(field.required)}
                            onChange={(event) =>
                              patchField(objectIndex, fieldIndex, {
                                required: event.target.checked,
                              })
                            }
                          >
                            {t("features.data.required")}
                          </Checkbox>
                          <Button
                            type="text"
                            danger
                            size="small"
                            icon={<Trash2 size={13} />}
                            onClick={() =>
                              setObjects((current) =>
                                current.map((item, at) =>
                                  at === objectIndex
                                    ? {
                                        ...item,
                                        fields: (item.fields ?? []).filter(
                                          (_, atField) =>
                                            atField !== fieldIndex,
                                        ),
                                      }
                                    : item,
                                ),
                              )
                            }
                          />
                        </Space>
                        {fieldType === "enum" ? (
                          <Input
                            style={{ marginTop: 8 }}
                            addonBefore={t("features.data.enumOptions")}
                            placeholder={t("features.data.enumOptionsHint")}
                            value={(field.options ?? []).join(", ")}
                            onChange={(event) =>
                              patchField(objectIndex, fieldIndex, {
                                options: parseCsv(event.target.value),
                              })
                            }
                          />
                        ) : null}
                        {fieldType === "date" || fieldType === "datetime" ? (
                          <Input
                            style={{ marginTop: 8 }}
                            addonBefore={t("features.data.reminderDays")}
                            placeholder="90, 60, 30"
                            value={(field.reminder_days ?? []).join(", ")}
                            onChange={(event) => {
                              const reminder_days = parseCsv(event.target.value)
                                .map((item) => Number(item))
                                .filter((item) => Number.isInteger(item));
                              patchField(objectIndex, fieldIndex, {
                                reminder_days,
                              });
                            }}
                          />
                        ) : null}
                        {fieldType === "record_ref" ? (
                          <Space wrap style={{ width: "100%", marginTop: 8 }}>
                            <Select
                              aria-label={t("features.data.targetFeature")}
                              placeholder={t("features.data.targetFeature")}
                              value={targetAgentId}
                              options={[
                                {
                                  value: agentId,
                                  label: t("features.data.thisFeature"),
                                },
                                ...featureAgents
                                  .filter(
                                    (candidate) =>
                                      candidate.agent_id !== agentId,
                                  )
                                  .map((candidate) => ({
                                    value: candidate.agent_id,
                                    label: candidate.name,
                                  })),
                              ]}
                              onChange={(nextAgentId) => {
                                const target_agent_id =
                                  nextAgentId === agentId
                                    ? undefined
                                    : nextAgentId;
                                patchField(objectIndex, fieldIndex, {
                                  target_agent_id,
                                  target_object: undefined,
                                });
                                if (target_agent_id)
                                  void featureDataApi
                                    .getSchema(target_agent_id)
                                    .then((answer) =>
                                      setReferencedObjects((current) => ({
                                        ...current,
                                        [target_agent_id]:
                                          answer.schema?.objects ?? [],
                                      })),
                                    )
                                    .catch(() =>
                                      setReferencedObjects((current) => ({
                                        ...current,
                                        [target_agent_id]: [],
                                      })),
                                    );
                              }}
                            />
                            <Select
                              aria-label={t("features.data.targetObject")}
                              placeholder={t("features.data.targetObject")}
                              value={field.target_object || undefined}
                              options={targetObjectOptions.map((candidate) => ({
                                value: candidate.key,
                                label:
                                  nameOf(candidate.name).zh || candidate.key,
                              }))}
                              onFocus={() => {
                                if (
                                  targetAgentId !== agentId &&
                                  !referencedObjects[targetAgentId]
                                )
                                  void featureDataApi
                                    .getSchema(targetAgentId)
                                    .then((answer) =>
                                      setReferencedObjects((current) => ({
                                        ...current,
                                        [targetAgentId]:
                                          answer.schema?.objects ?? [],
                                      })),
                                    )
                                    .catch(() =>
                                      setReferencedObjects((current) => ({
                                        ...current,
                                        [targetAgentId]: [],
                                      })),
                                    );
                              }}
                              onChange={(target_object) =>
                                patchField(objectIndex, fieldIndex, {
                                  target_object,
                                })
                              }
                            />
                          </Space>
                        ) : null}
                        {fieldType === "date" || fieldType === "datetime" ? (
                          <Space wrap style={{ width: "100%", marginTop: 8 }}>
                            <Select
                              aria-label={t("features.data.computedOperation")}
                              allowClear
                              placeholder={t("features.data.computedOperation")}
                              value={field.computed?.op}
                              options={[
                                "add_months",
                                "add_years",
                                "add_days",
                              ].map((op) => ({
                                value: op,
                                label: t(`features.data.computedOps.${op}`),
                              }))}
                              onChange={(op) =>
                                patchField(objectIndex, fieldIndex, {
                                  computed: op
                                    ? {
                                        ...(field.computed ?? {
                                          source_field: "",
                                          amount: 0,
                                        }),
                                        op: op as
                                          | "add_months"
                                          | "add_years"
                                          | "add_days",
                                      }
                                    : undefined,
                                })
                              }
                            />
                            {field.computed ? (
                              <>
                                <Select
                                  aria-label={t("features.data.computedSource")}
                                  placeholder={t(
                                    "features.data.computedSource",
                                  )}
                                  value={
                                    field.computed.source_field || undefined
                                  }
                                  options={fields
                                    .filter(
                                      (candidate) =>
                                        candidate.key !== field.key &&
                                        ["date", "datetime"].includes(
                                          candidate.type,
                                        ),
                                    )
                                    .map((candidate) => ({
                                      value: candidate.key,
                                      label: candidate.key,
                                    }))}
                                  onChange={(source_field) =>
                                    patchField(objectIndex, fieldIndex, {
                                      computed: {
                                        ...field.computed!,
                                        source_field,
                                      },
                                    })
                                  }
                                />
                                <Select
                                  aria-label={t(
                                    "features.data.computedAmountField",
                                  )}
                                  allowClear
                                  placeholder={t(
                                    "features.data.computedAmountField",
                                  )}
                                  value={field.computed.amount_field}
                                  options={fields
                                    .filter(
                                      (candidate) =>
                                        candidate.key !== field.key &&
                                        candidate.type === "integer",
                                    )
                                    .map((candidate) => ({
                                      value: candidate.key,
                                      label: candidate.key,
                                    }))}
                                  onChange={(amount_field) =>
                                    patchField(objectIndex, fieldIndex, {
                                      computed: {
                                        ...field.computed!,
                                        amount_field,
                                        amount: amount_field
                                          ? undefined
                                          : field.computed!.amount ?? 0,
                                      },
                                    })
                                  }
                                />
                                {!field.computed.amount_field ? (
                                  <Input
                                    type="number"
                                    aria-label={t(
                                      "features.data.computedAmount",
                                    )}
                                    placeholder={t(
                                      "features.data.computedAmount",
                                    )}
                                    value={field.computed.amount ?? ""}
                                    onChange={(event) =>
                                      patchField(objectIndex, fieldIndex, {
                                        computed: {
                                          ...field.computed!,
                                          amount:
                                            event.target.value === ""
                                              ? undefined
                                              : Number(event.target.value),
                                        },
                                      })
                                    }
                                  />
                                ) : null}
                              </>
                            ) : null}
                          </Space>
                        ) : null}
                      </Card>
                    );
                  })}
                  <Button
                    type="dashed"
                    icon={<Plus size={14} />}
                    onClick={() =>
                      patchObject(objectIndex, {
                        fields: [...fields, emptyField(fields.length + 1)],
                      })
                    }
                  >
                    {t("features.data.addField")}
                  </Button>
                </Space>
              </div>
            </Card>
          );
        })}

        <Button
          type="dashed"
          icon={<Plus size={14} />}
          onClick={() =>
            setObjects((current) => [
              ...current,
              emptyObject(current.length + 1),
            ])
          }
        >
          {t("features.data.addObject")}
        </Button>
      </Space>

      <div className={styles.footer}>
        {saving ? <Spin size="small" /> : null}
        <Button type="primary" loading={saving} onClick={() => void save()}>
          {t("features.data.saveSchema")}
        </Button>
      </div>
    </div>
  );
}
