import { useEffect, useRef, useState } from "react";
import { Input, InputNumber, Select, Space, Switch } from "antd";
import { useTranslation } from "react-i18next";

import type {
  FeatureDataField,
  FeatureDataObject,
  FeatureDataPrincipal,
} from "../../../api/modules/featureData";
import { featureDataApi } from "../../../api/modules/featureData";
import {
  knowledgeBasesApi,
  type KnowledgeBase,
  type KnowledgeDocument,
} from "../../../api/modules/knowledgeBases";
import { fieldLabel, localizedText, valueFields } from "../utils/featureData";
import styles from "./FeatureDataPanel.module.less";

export interface FeatureDataRecordFieldsProps {
  agentId: string;
  object: FeatureDataObject;
  values: Record<string, unknown>;
  readOnly?: boolean;
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
  agentId,
  object,
  values,
  readOnly = false,
  missing = [],
  onChange,
}: FeatureDataRecordFieldsProps) {
  const { t, i18n } = useTranslation();
  const language = i18n.language ?? "en";
  const fields = valueFields(object);
  const [principals, setPrincipals] = useState<FeatureDataPrincipal[]>([]);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [documentsByKb, setDocumentsByKb] = useState<
    Record<string, KnowledgeDocument[]>
  >({});
  const [selectedKb, setSelectedKb] = useState("");
  const pendingKb = useRef<Set<string> | null>(null);
  const [remoteRecords, setRemoteRecords] = useState<
    Record<string, { value: string; label: string }[]>
  >({});
  const unitOptions = Array.from(
    new Map(
      principals
        .filter((principal) => principal.org_unit)
        .map((principal) => [
          principal.org_unit!,
          {
            value: principal.org_unit!,
            label: localizedText(
              principal.org_unit_label,
              language,
              principal.org_unit!,
            ),
          },
        ]),
    ).values(),
  );
  useEffect(() => {
    if (
      (object.fields ?? []).some(
        (field) => field.type === "user" || field.type === "org_unit",
      )
    ) {
      void featureDataApi
        .listPrincipals(agentId, { limit: 200 })
        .then((answer) => setPrincipals(answer.principals ?? []))
        .catch(() => setPrincipals([]));
    }
    if ((object.fields ?? []).some((field) => field.type === "knowledge_ref")) {
      void knowledgeBasesApi
        .list()
        .then(setBases)
        .catch(() => setBases([]));
    }
  }, [agentId, object.fields]);
  useEffect(() => {
    const pending = (pendingKb.current ??= new Set<string>());
    const kbIds = new Set<string>(selectedKb ? [selectedKb] : []);
    for (const field of object.fields ?? []) {
      const value = values[field.key];
      if (
        field.type === "knowledge_ref" &&
        value !== null &&
        typeof value === "object"
      ) {
        const kbId = (value as Record<string, unknown>).kb_id;
        if (typeof kbId === "string" && kbId) kbIds.add(kbId);
      }
    }
    for (const kbId of kbIds) {
      if (documentsByKb[kbId] || pending.has(kbId)) continue;
      pending.add(kbId);
      void knowledgeBasesApi
        .listDocuments(kbId)
        .then((documents) =>
          setDocumentsByKb((current) => ({ ...current, [kbId]: documents })),
        )
        .catch(() =>
          setDocumentsByKb((current) => ({ ...current, [kbId]: [] })),
        )
        .finally(() => pending.delete(kbId));
    }
  }, [object.fields, values, selectedKb, documentsByKb]);
  if (fields.length === 0)
    return <p className={styles.hint}>{t("features.data.noFields")}</p>;
  const emit = (field: FeatureDataField, value: unknown) =>
    onChange?.(field.key, value);
  const renderControl = (field: FeatureDataField) => {
    const value = values[field.key];
    const disabled = readOnly || field.computed !== undefined;
    const label = fieldLabel(field, language);
    if (field.computed)
      return <Input aria-label={label} value={textValue(value)} disabled />;
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
      case "user": {
        const userId = numberValue(value);
        const userOptions = principals.map((principal) => ({
          value: principal.id,
          label: principal.display_name || principal.username,
        }));
        if (
          userId !== null &&
          !userOptions.some((option) => option.value === userId)
        )
          userOptions.push({ value: userId, label: `#${userId}` });
        return (
          <Select
            aria-label={label}
            allowClear
            showSearch
            filterOption={false}
            value={userId ?? undefined}
            disabled={disabled}
            options={userOptions}
            onSearch={(q) => {
              void featureDataApi
                .listPrincipals(agentId, { q, limit: 200 })
                .then((answer) => setPrincipals(answer.principals ?? []))
                .catch(() => setPrincipals([]));
            }}
            onChange={(next) => emit(field, next ?? null)}
          />
        );
      }
      case "org_unit": {
        const unitKey = typeof value === "string" ? value : "";
        const options = unitOptions.map((unit) => ({ ...unit }));
        if (unitKey && !options.some((option) => option.value === unitKey))
          options.push({ value: unitKey, label: unitKey });
        return (
          <Select
            aria-label={label}
            allowClear
            showSearch
            filterOption={false}
            value={unitKey || undefined}
            disabled={disabled}
            options={options}
            onSearch={(q) => {
              void featureDataApi
                .listPrincipals(agentId, { q, limit: 200 })
                .then((answer) => setPrincipals(answer.principals ?? []))
                .catch(() => setPrincipals([]));
            }}
            onChange={(next) => emit(field, next ?? null)}
          />
        );
      }
      case "record_ref": {
        const recordId =
          typeof value === "string"
            ? value
            : String(
                (value as Record<string, unknown> | null)?.record_id ?? "",
              );
        const loadOptions = async (search: string) => {
          const answer = await featureDataApi.listRecordReferenceOptions(
            agentId,
            {
              target_agent_id: field.target_agent_id,
              object_key: field.target_object,
              q: search,
              limit: 50,
            },
          );
          return answer.records.map((record) => ({
            value: record.id,
            label: record.title,
          }));
        };
        return (
          <Select
            aria-label={label}
            allowClear
            showSearch
            filterOption={false}
            disabled={disabled}
            value={recordId || undefined}
            onSearch={(search) => {
              void loadOptions(search)
                .then((options) =>
                  setRemoteRecords((current) => ({
                    ...current,
                    [field.key]: options,
                  })),
                )
                .catch(() =>
                  setRemoteRecords((current) => ({
                    ...current,
                    [field.key]: [],
                  })),
                );
            }}
            onFocus={() => {
              if (!remoteRecords[field.key])
                void loadOptions("")
                  .then((options) =>
                    setRemoteRecords((current) => ({
                      ...current,
                      [field.key]: options,
                    })),
                  )
                  .catch(() => undefined);
            }}
            options={
              remoteRecords[field.key] ??
              (recordId ? [{ value: recordId, label: recordId }] : [])
            }
            onChange={(next) => emit(field, next ?? null)}
          />
        );
      }
      case "knowledge_ref": {
        const ref =
          value && typeof value === "object"
            ? (value as Record<string, unknown>)
            : {};
        const kbId = String(ref.kb_id ?? selectedKb);
        return (
          <div style={{ display: "grid", gap: 8 }}>
            <Select
              aria-label={label}
              allowClear
              placeholder={t("features.data.selectKnowledgeBase")}
              value={kbId || undefined}
              options={bases.map((base) => ({
                value: base.id,
                label: base.name,
              }))}
              disabled={disabled}
              onChange={(nextKb) => {
                setSelectedKb(nextKb ?? "");
                emit(field, nextKb ? { kb_id: nextKb } : null);
              }}
            />
            <Select
              aria-label={t("features.data.selectKnowledgeDocument")}
              allowClear
              placeholder={t("features.data.selectKnowledgeDocument")}
              value={String(ref.document_id ?? "") || undefined}
              options={(documentsByKb[kbId] ?? [])
                .filter((document) => !document.is_dir)
                .map((document) => ({
                  value: document.id,
                  label: document.path || document.filename,
                }))}
              disabled={disabled || !kbId}
              onChange={(documentId) => {
                if (!documentId) {
                  emit(field, null);
                  return;
                }
                const document = (documentsByKb[kbId] ?? []).find(
                  (item) => item.id === documentId,
                );
                emit(field, {
                  ...ref,
                  kb_id: kbId,
                  document_id: documentId,
                  version: document?.content_hash,
                });
              }}
            />
            <Space size="small">
              <InputNumber
                aria-label={t("features.data.page")}
                min={1}
                placeholder={t("features.data.page")}
                value={typeof ref.page === "number" ? ref.page : undefined}
                disabled={disabled}
                onChange={(page) =>
                  emit(field, { ...ref, kb_id: kbId, page: page ?? undefined })
                }
              />
              <Input
                aria-label={t("features.data.layout")}
                placeholder={t("features.data.layout")}
                value={typeof ref.layout === "string" ? ref.layout : ""}
                disabled={disabled}
                onChange={(event) =>
                  emit(field, {
                    ...ref,
                    kb_id: kbId,
                    layout: event.target.value || undefined,
                  })
                }
              />
            </Space>
          </div>
        );
      }
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
  const missingKeys = new Set(missing);
  return (
    <div className={styles.fields}>
      {fields.map((field) => {
        const label = fieldLabel(field, language);
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
            {field.type === "org_unit" ? (
              <small>{t("features.data.orgUnitFieldHint")}</small>
            ) : null}
            {renderControl(field)}
            {missingKeys.has(field.key) ? (
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
