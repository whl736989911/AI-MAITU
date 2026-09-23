/**
 * 输入 — the form a feature's thread asks for before a run starts.
 *
 * The fields are ``inputs.properties``, in the order the definition declares them,
 * and each one is asked under its own ``title`` in the reader's language: the
 * author wrote this form for their callers, so the card asks it as written rather
 * than translating it a second time.
 *
 * **One run, one turn.** Pressing 运行 sends an ordinary chat turn — the uploaded
 * files ride it as attachments, and the submitted values ride it as the frame's
 * own ``feature_run`` field, which is how the platform records the run and renders
 * the values into that turn's prompt. The button stays disabled until every
 * ``required`` field is answered; afterwards the card is read-only, so a
 * conversation keeps the values it ran under next to what they produced.
 *
 * **A file field uploads through the chat's own path** (the workspace's
 * ``inbound/``), so a run's files are reachable by the same tools and the same
 * previews as any other attachment — nothing here talks to a second mechanism.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Input, InputNumber, Select, Switch, Tooltip } from "antd";
import type { TFunction } from "i18next";
import { Paperclip, Play, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type {
  WorkflowInputField,
  WorkflowInputs,
} from "../../../api/modules/featureWorkflow";
import { uploadFile } from "../../../api/modules/upload";
import { useServerUploadLimit } from "../../../hooks/useServerUploadLimit";
import { apiErrorMessage } from "../../../utils/apiError";
import { message as antMessage } from "../../../utils/antdMessage";
import { pickLocale } from "../../../utils/localizedText";
import type { UiLocale } from "../../../utils/locale";
import type { ChatAttachment } from "../hooks/useChat";
import type { ThreadRun } from "../hooks/useWorkflowRun";
import { inferAttachmentKind } from "../utils/chatAttachments";
import {
  emptyInputDraft,
  filenameOf,
  missingRequiredInputs,
  submittedInputs,
  type FeatureRunPayload,
  type WorkflowInputDraft,
  type WorkflowInputValue,
} from "../utils/featureRun";
import styles from "./WorkflowInputCard.module.less";

/** What pressing 运行 hands to the thread: one turn, and the run it carries. */
export interface WorkflowRunSubmission {
  /** The uploaded files, as the composer would send them. */
  attachments: ChatAttachment[];
  /** What the turn carries as its ``feature_run`` field for the platform to read. */
  payload: FeatureRunPayload;
}

export interface WorkflowInputCardProps {
  /** The declared form. */
  inputs: WorkflowInputs;
  /** The run this thread already has — the card is read-only when there is one. */
  run: ThreadRun | null;
  agentId: string;
  /** A turn is in flight (or the thread is being created): no second run. */
  busy: boolean;
  onRun: (submission: WorkflowRunSubmission) => void;
}

/** The label a field is asked under, in the reader's language. */
function fieldLabel(field: WorkflowInputField, locale: UiLocale): string {
  return pickLocale(field.title, locale) || field.title.en || field.title.zh;
}

/** One submitted value as text — the read-only card's answer to a field. */
function displayValue(
  field: WorkflowInputField,
  value: unknown,
  t: TFunction,
): string {
  if (field.type === "boolean") {
    return t(value ? "chat.workflow.valueYes" : "chat.workflow.valueNo");
  }
  if (value === undefined || value === null || value === "") return "—";
  if (field.type === "file") {
    // A file field answers with workspace paths; the caller reads names.
    return (Array.isArray(value) ? value : [value])
      .map((path) => filenameOf(String(path)))
      .join(", ");
  }
  if (Array.isArray(value)) {
    return value.map((item) => String(item)).join(", ");
  }
  return String(value);
}

export default function WorkflowInputCard({
  inputs,
  run,
  agentId,
  busy,
  onRun,
}: WorkflowInputCardProps) {
  const { t, i18n } = useTranslation();
  const locale: UiLocale = i18n.language?.startsWith("zh") ? "zh" : "en";
  const { maxUploadBytes, maxUploadMb } = useServerUploadLimit();
  const [draft, setDraft] = useState<WorkflowInputDraft>(() =>
    emptyInputDraft(inputs),
  );
  const [files, setFiles] = useState<Record<string, ChatAttachment[]>>({});
  const [uploading, setUploading] = useState<Record<string, boolean>>({});
  const fileInputs = useRef<Record<string, HTMLInputElement | null>>({});

  // The form is a fresh one whenever the definition it asks changes.
  useEffect(() => {
    setDraft(emptyInputDraft(inputs));
    setFiles({});
  }, [inputs]);

  const fields = useMemo(() => Object.entries(inputs.properties), [inputs]);
  const missing = useMemo(
    () => missingRequiredInputs(inputs, draft),
    [inputs, draft],
  );
  const uploadingAny = Object.values(uploading).some(Boolean);
  const readOnly = run !== null;

  const setValue = (name: string, value: WorkflowInputValue) => {
    setDraft((current) => ({ ...current, [name]: value }));
  };

  const addFiles = async (name: string, picked: FileList | File[]) => {
    const accepted = Array.from(picked).filter((file) => {
      if (file.size <= maxUploadBytes) return true;
      antMessage.error(
        t("upload.tooLarge", "File too large (max {{maxMb}}MB): {{name}}", {
          name: file.name,
          maxMb: maxUploadMb,
        }),
      );
      return false;
    });
    if (accepted.length === 0) return;

    setUploading((current) => ({ ...current, [name]: true }));
    try {
      const uploaded = await Promise.all(
        accepted.map(async (file) => {
          const response = await uploadFile(agentId, file);
          return {
            url: response.access_url || response.url,
            filename: response.filename,
            mediaType: response.media_type,
            workspacePath: response.path || response.workspace_path,
            kind: inferAttachmentKind(file, response.media_type),
          } satisfies ChatAttachment;
        }),
      );
      // A single-file field holds one file: picking another replaces the first,
      // which is what the caller picking a second file is asking for.
      const combined = [...(files[name] ?? []), ...uploaded];
      const kept =
        inputs.properties[name]?.multiple === true
          ? combined
          : combined.slice(-1);
      setFiles((current) => ({ ...current, [name]: kept }));
      setValue(
        name,
        kept.map((file) => file.workspacePath ?? "").filter(Boolean),
      );
    } catch (error: unknown) {
      antMessage.error(
        apiErrorMessage(error, t("upload.failed", "Upload failed"), t),
      );
    } finally {
      setUploading((current) => ({ ...current, [name]: false }));
    }
  };

  const removeFile = (name: string, index: number) => {
    const kept = (files[name] ?? []).filter((_, i) => i !== index);
    setFiles((current) => ({ ...current, [name]: kept }));
    setValue(
      name,
      kept.map((file) => file.workspacePath ?? "").filter(Boolean),
    );
  };

  const submit = () => {
    if (readOnly || busy || uploadingAny || missing.length > 0) return;
    const attachments = fields.flatMap(([name]) => files[name] ?? []);
    onRun({
      attachments,
      payload: {
        inputs: submittedInputs(inputs, draft),
        attachments: attachments
          .map((file) => file.workspacePath ?? "")
          .filter(Boolean),
      },
    });
  };

  return (
    <section className={styles.card} aria-label={t("chat.workflow.inputTitle")}>
      <div className={styles.header}>
        <span className={styles.title}>{t("chat.workflow.inputTitle")}</span>
        {readOnly ? (
          <span className={styles.badge}>{t("chat.workflow.submitted")}</span>
        ) : null}
      </div>

      <div className={styles.fields}>
        {fields.map(([name, field]) => {
          const required = (inputs.required ?? []).includes(name);
          return (
            <div className={styles.field} key={name}>
              <label className={styles.fieldLabel} htmlFor={`wf-${name}`}>
                {fieldLabel(field, locale)}
                {required ? <span className={styles.required}>*</span> : null}
              </label>
              {field.description ? (
                <p className={styles.fieldHint}>
                  {pickLocale(field.description, locale)}
                </p>
              ) : null}

              {readOnly ? (
                <p className={styles.readOnlyValue}>
                  {displayValue(field, run?.inputs[name], t)}
                </p>
              ) : (
                <FieldControl
                  id={`wf-${name}`}
                  field={field}
                  value={draft[name]}
                  disabled={busy}
                  t={t}
                  onChange={(next) => setValue(name, next)}
                />
              )}

              {!readOnly && field.type === "file" ? (
                <div className={styles.fileField}>
                  <input
                    ref={(element) => {
                      fileInputs.current[name] = element;
                    }}
                    type="file"
                    className={styles.fileInput}
                    accept={field.accept || undefined}
                    multiple={field.multiple === true}
                    onChange={(event) => {
                      const picked = event.target.files;
                      if (picked && picked.length > 0) {
                        void addFiles(name, picked);
                      }
                      event.target.value = "";
                    }}
                  />
                  <Button
                    id={`wf-${name}`}
                    size="small"
                    icon={<Paperclip size={13} />}
                    loading={uploading[name] === true}
                    disabled={busy}
                    onClick={() => fileInputs.current[name]?.click()}
                  >
                    {t("chat.workflow.chooseFile")}
                  </Button>
                  {field.accept ? (
                    <span className={styles.fileHint}>{field.accept}</span>
                  ) : null}
                  <ul className={styles.fileList}>
                    {(files[name] ?? []).map((file, index) => (
                      <li
                        className={styles.fileRow}
                        key={`${file.url}-${index}`}
                      >
                        <span className={styles.fileName}>
                          {file.filename || file.workspacePath}
                        </span>
                        <Button
                          type="text"
                          size="small"
                          danger
                          disabled={busy}
                          icon={<Trash2 size={13} />}
                          aria-label={t("chat.workflow.removeFile", {
                            name: file.filename ?? "",
                          })}
                          onClick={() => removeFile(name, index)}
                        />
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {readOnly ? null : (
        <div className={styles.actions}>
          <Tooltip
            title={
              missing.length > 0
                ? t("chat.workflow.missingRequired", { count: missing.length })
                : undefined
            }
          >
            <span>
              <Button
                type="primary"
                icon={<Play size={13} />}
                disabled={missing.length > 0 || busy || uploadingAny}
                onClick={submit}
              >
                {t("chat.workflow.run")}
              </Button>
            </span>
          </Tooltip>
        </div>
      )}
    </section>
  );
}

/** One field's control, by the type the definition gives it. */
function FieldControl({
  id,
  field,
  value,
  disabled,
  t,
  onChange,
}: {
  id: string;
  field: WorkflowInputField;
  value: WorkflowInputValue | undefined;
  disabled: boolean;
  t: TFunction;
  onChange: (value: WorkflowInputValue) => void;
}) {
  if (field.type === "boolean") {
    return (
      <Switch
        id={id}
        checked={value === true}
        disabled={disabled}
        checkedChildren={t("chat.workflow.valueYes")}
        unCheckedChildren={t("chat.workflow.valueNo")}
        onChange={(checked) => onChange(checked)}
      />
    );
  }

  if (field.type === "number" || field.type === "integer") {
    return (
      <InputNumber
        id={id}
        className={styles.numberInput}
        value={typeof value === "number" ? value : null}
        disabled={disabled}
        precision={field.type === "integer" ? 0 : undefined}
        step={field.type === "integer" ? 1 : undefined}
        onChange={(next) => onChange(typeof next === "number" ? next : null)}
      />
    );
  }

  if (field.type === "array") {
    const items = Array.isArray(value) ? value : [];
    return (
      <Select
        id={id}
        mode="tags"
        className={styles.wide}
        value={items.map(String)}
        disabled={disabled}
        tokenSeparators={[","]}
        placeholder={t("chat.workflow.arrayPlaceholder")}
        onChange={(next: string[]) => onChange(next)}
      />
    );
  }

  if (field.type === "file") {
    // A file field's widget is the upload row below it — the paths it holds are
    // an implementation detail the caller never types.
    return null;
  }

  const text = typeof value === "string" ? value : "";
  if (field.enum && field.enum.length > 0) {
    return (
      <Select
        id={id}
        className={styles.wide}
        value={text || undefined}
        disabled={disabled}
        placeholder={t("chat.workflow.selectPlaceholder")}
        options={field.enum.map((option) => ({ value: option, label: option }))}
        onChange={(next: string) => onChange(next ?? "")}
      />
    );
  }
  if (field.format === "textarea") {
    return (
      <Input.TextArea
        id={id}
        value={text}
        disabled={disabled}
        autoSize={{ minRows: 2, maxRows: 6 }}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  return (
    <Input
      id={id}
      className={styles.wide}
      type={
        field.format === "date"
          ? "date"
          : field.format === "email"
          ? "email"
          : "text"
      }
      value={text}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}
