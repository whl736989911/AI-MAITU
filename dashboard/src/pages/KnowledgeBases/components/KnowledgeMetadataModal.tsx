import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Typography,
  message,
} from "antd";
import { useTranslation } from "react-i18next";

import {
  knowledgeBasesApi,
  type KnowledgeDocument,
  type KnowledgeMetadataField,
} from "../../../api/modules/knowledgeBases";
import { apiErrorMessage } from "../../../utils/apiError";
import { metadataValuesForSchema } from "../knowledgeMetadata";

interface Props {
  open: boolean;
  baseId: string;
  schema: KnowledgeMetadataField[];
  document?: KnowledgeDocument;
  onClose: () => void;
  onSchemaSaved?: (fields: KnowledgeMetadataField[]) => void;
  onDocumentSaved?: (docId: string, metadata: Record<string, unknown>) => void;
}

const FIELD_TYPES = ["text", "number", "date", "enum"] as const;

function emptyField(): KnowledgeMetadataField {
  return {
    key: "",
    label: "",
    type: "text",
    options: [],
    required: false,
    searchable: true,
  };
}

export default function KnowledgeMetadataModal({
  open,
  baseId,
  schema,
  document: target,
  onClose,
  onSchemaSaved,
  onDocumentSaved,
}: Props) {
  const { t } = useTranslation();
  const editingDocument = Boolean(target);
  const [fields, setFields] = useState<KnowledgeMetadataField[]>(schema);
  const [optionText, setOptionText] = useState<Record<string, string>>({});
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (editingDocument) {
      setValues({ ...(target?.metadata ?? {}) });
      return;
    }
    setFields(schema);
    setOptionText(
      Object.fromEntries(
        schema.map((field) => [field.key, (field.options ?? []).join(", ")]),
      ),
    );
  }, [editingDocument, open, schema, target]);

  const title = editingDocument
    ? t("knowledgeBases.metadata.documentTitle")
    : t("knowledgeBases.metadata.schemaTitle");

  const updateField = (index: number, patch: Partial<KnowledgeMetadataField>) => {
    const previousKey = fields[index]?.key;
    const nextKey = patch.key;
    setFields((current) =>
      current.map((field, idx) => (idx === index ? { ...field, ...patch } : field)),
    );
    if (nextKey !== undefined && previousKey !== undefined && nextKey !== previousKey) {
      setOptionText((current) => {
        if (!(previousKey in current)) return current;
        const { [previousKey]: moved, ...rest } = current;
        return { ...rest, [nextKey]: moved };
      });
    }
  };

  const schemaPayload = useMemo(
    () =>
      fields.map((field) => ({
        ...field,
        options: (optionText[field.key] ?? field.options.join(", "))
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
      })),
    [fields, optionText],
  );

  const save = async () => {
    setSaving(true);
    try {
      if (editingDocument && target) {
        const result = await knowledgeBasesApi.updateDocumentMetadata(
          baseId,
          target.id,
          metadataValuesForSchema(values, schema),
        );
        onDocumentSaved?.(target.id, result.metadata);
        message.success(t("knowledgeBases.metadata.documentSaved"));
      } else {
        const result = await knowledgeBasesApi.putMetadataSchema(baseId, schemaPayload);
        onSchemaSaved?.(result.fields);
        message.success(t("knowledgeBases.metadata.saved"));
      }
      onClose();
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.metadata.saveFailed"), t),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={title}
      onCancel={onClose}
      onOk={() => void save()}
      confirmLoading={saving}
      okText={t("knowledgeBases.metadata.save")}
      width={editingDocument ? 520 : 760}
    >
      {editingDocument ? (
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          {schema.length === 0 ? (
            <Typography.Text type="secondary">
              {t("knowledgeBases.metadata.emptyFields")}
            </Typography.Text>
          ) : (
            schema.map((field) => (
              <label key={field.key} style={{ display: "block" }}>
                <Typography.Text strong>{field.label || field.key}</Typography.Text>
                {field.type === "enum" ? (
                  <Select
                    allowClear
                    style={{ width: "100%", marginTop: 4 }}
                    value={
                      typeof values[field.key] === "string"
                        ? (values[field.key] as string)
                        : undefined
                    }
                    options={(field.options ?? []).map((option) => ({
                      value: option,
                      label: option,
                    }))}
                    onChange={(value) =>
                      setValues((current) => ({
                        ...current,
                        [field.key]: value ?? "",
                      }))
                    }
                  />
                ) : (
                  <Input
                    style={{ marginTop: 4 }}
                    type={
                      field.type === "number"
                        ? "number"
                        : field.type === "date"
                        ? "date"
                        : "text"
                    }
                    value={
                      values[field.key] === undefined
                        ? ""
                        : String(values[field.key])
                    }
                    onChange={(event) => {
                      const raw = event.target.value;
                      setValues((current) => ({
                        ...current,
                        [field.key]:
                          field.type === "number" && raw !== ""
                            ? Number(raw)
                            : raw,
                      }));
                    }}
                  />
                )}
              </label>
            ))
          )}
        </Space>
      ) : (
        <Space direction="vertical" style={{ width: "100%" }} size={10}>
          {fields.length === 0 ? (
            <Typography.Text type="secondary">
              {t("knowledgeBases.metadata.emptyFields")}
            </Typography.Text>
          ) : null}
          {fields.map((field, index) => (
            <Space key={index} align="start" wrap>
              <Input
                style={{ width: 130 }}
                placeholder={t("knowledgeBases.metadata.key")}
                value={field.key}
                onChange={(event) => updateField(index, { key: event.target.value })}
              />
              <Input
                style={{ width: 150 }}
                placeholder={t("knowledgeBases.metadata.label")}
                value={field.label}
                onChange={(event) => updateField(index, { label: event.target.value })}
              />
              <Select
                style={{ width: 110 }}
                value={field.type}
                options={FIELD_TYPES.map((type) => ({
                  value: type,
                  label: t(`knowledgeBases.metadata.${type}`),
                }))}
                onChange={(type) => updateField(index, { type })}
              />
              {field.type === "enum" ? (
                <Input
                  style={{ width: 180 }}
                  placeholder={t("knowledgeBases.metadata.options")}
                  value={optionText[field.key] ?? field.options.join(", ")}
                  onChange={(event) =>
                    setOptionText((current) => ({
                      ...current,
                      [field.key]: event.target.value,
                    }))
                  }
                />
              ) : null}
              <Space size={4}>
                <Typography.Text>
                  {t("knowledgeBases.metadata.required")}
                </Typography.Text>
                <Switch
                  size="small"
                  checked={field.required}
                  onChange={(required) => updateField(index, { required })}
                />
              </Space>
              <Space size={4}>
                <Typography.Text>
                  {t("knowledgeBases.metadata.searchable")}
                </Typography.Text>
                <Switch
                  size="small"
                  checked={field.searchable}
                  onChange={(searchable) => updateField(index, { searchable })}
                />
              </Space>
              <Button
                danger
                type="text"
                onClick={() =>
                  setFields((current) => current.filter((_, idx) => idx !== index))
                }
              >
                {t("knowledgeBases.metadata.remove")}
              </Button>
            </Space>
          ))}
          <Button onClick={() => setFields((current) => [...current, emptyField()])}>
            {t("knowledgeBases.metadata.addField")}
          </Button>
        </Space>
      )}
    </Modal>
  );
}
