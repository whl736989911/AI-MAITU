/**
 * Versioned attachments for one record, grouped by the schema's ``file`` fields.
 *
 * A ``file`` value is not part of ``values``: the bytes live in the feature's
 * workspace and are addressed by these endpoints. So this section is the only
 * place a file field appears — with its current version, its note, and every
 * earlier version kept for audit. Uploading uses ``FormData`` (the browser owns
 * the multipart boundary) and a note that says what changed in this version.
 *
 * The record's revision changes on upload, so the parent reloads it instead of
 * keeping a stale copy that the next save would be refused against.
 */

import { useState } from "react";
import { Button, Empty, Tag, Tooltip } from "antd";
import { DownloadOutlined, UploadOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";

import { message } from "@/utils/antdMessage";

import type {
  FeatureDataFile,
  FeatureDataObject,
  FeatureDataRecord,
} from "../../../api/modules/featureData";
import { featureDataApi } from "../../../api/modules/featureData";
import { apiErrorMessage } from "../../../utils/apiError";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import {
  currentFile,
  fieldLabel,
  fileFields,
  filesByField,
  formatFileSize,
} from "../utils/featureData";
import styles from "./FeatureDataPanel.module.less";

export interface FeatureDataAttachmentsProps {
  agentId: string;
  object: FeatureDataObject;
  record: FeatureDataRecord;
  canWrite: boolean;
  /** Reload the record: an upload is a new revision. */
  onChanged: () => void;
}

/** Save one downloaded blob under its server filename. */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename || "download";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** One version row: identity, size, note, time and a download action. */
function VersionRow({
  file,
  timeZone,
  onDownload,
  labels,
}: {
  file: FeatureDataFile;
  timeZone: string;
  onDownload: () => void;
  labels: {
    version: (version: number) => string;
    current: string;
    download: string;
  };
}) {
  return (
    <div className={styles.fileRow}>
      <div className={styles.fileMain}>
        <span className={styles.fileName}>{file.filename}</span>
        {file.current ? (
          <Tag color="green" bordered={false}>
            {labels.current}
          </Tag>
        ) : null}
        <span className={styles.fileMeta}>
          {labels.version(file.version)} · {formatFileSize(file.size)} ·{" "}
          {formatServerDateTime(file.created_at, timeZone)}
        </span>
        {file.note?.trim() ? (
          <span className={styles.fileNote}>{file.note}</span>
        ) : null}
      </div>
      <Tooltip title={labels.download}>
        <Button
          size="small"
          type="text"
          icon={<DownloadOutlined />}
          aria-label={`${labels.download} ${file.filename}`}
          onClick={onDownload}
        />
      </Tooltip>
    </div>
  );
}

export default function FeatureDataAttachments({
  agentId,
  object,
  record,
  canWrite,
  onChanged,
}: FeatureDataAttachmentsProps) {
  const { t, i18n } = useTranslation();
  const language = i18n.language ?? "en";
  const timeZone = useServerTimezone();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [uploading, setUploading] = useState<string | null>(null);
  const fields = fileFields(object);
  const grouped = filesByField(record.files);

  if (fields.length === 0) return null;

  const handleDownload = async (file: FeatureDataFile) => {
    try {
      const blob = await featureDataApi.downloadFile(
        agentId,
        record.id,
        file.id,
      );
      saveBlob(blob, file.filename);
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("features.data.downloadFailed"), t),
      );
    }
  };

  const handleUpload = async (fieldKey: string, file: File) => {
    setUploading(fieldKey);
    try {
      await featureDataApi.uploadFile(
        agentId,
        record.id,
        fieldKey,
        file,
        notes[fieldKey],
      );
      setNotes((previous) => ({ ...previous, [fieldKey]: "" }));
      message.success(t("features.data.uploaded"));
      onChanged();
    } catch (error) {
      message.error(apiErrorMessage(error, t("features.data.uploadFailed"), t));
    } finally {
      setUploading(null);
    }
  };

  return (
    <div className={styles.attachments}>
      {fields.map((field) => {
        const label = fieldLabel(field, language);
        const versions = grouped[field.key] ?? [];
        const current = currentFile(versions);
        const inputId = `feature-data-file-${field.key}`;
        return (
          <div className={styles.attachmentField} key={field.key}>
            <div className={styles.attachmentHeader}>
              <span className={styles.fieldLabel}>{label}</span>
              {current === null ? (
                <span className={styles.fieldHint}>
                  {t("features.data.noAttachments")}
                </span>
              ) : null}
            </div>
            {versions.length === 0 ? (
              <Empty
                className={styles.fileEmpty}
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={t("features.data.noAttachments")}
              />
            ) : (
              <div className={styles.fileList}>
                {versions.map((file) => (
                  <VersionRow
                    key={file.id}
                    file={file}
                    timeZone={timeZone}
                    labels={{
                      version: (version) =>
                        t("features.data.versionLabel", { version }),
                      current: t("features.data.current"),
                      download: t("features.data.download"),
                    }}
                    onDownload={() => void handleDownload(file)}
                  />
                ))}
              </div>
            )}
            {canWrite ? (
              <div className={styles.uploadRow}>
                <input
                  className={styles.fileInput}
                  type="text"
                  value={notes[field.key] ?? ""}
                  placeholder={t("features.data.fileNotePlaceholder")}
                  aria-label={`${t("features.data.fileNote")} ${label}`}
                  onChange={(event) =>
                    setNotes((previous) => ({
                      ...previous,
                      [field.key]: event.target.value,
                    }))
                  }
                />
                <label className={styles.uploadTrigger} htmlFor={inputId}>
                  <UploadOutlined />
                  {uploading === field.key
                    ? t("features.data.uploading")
                    : t("features.data.uploadFile")}
                  <input
                    id={inputId}
                    className={styles.fileInputHidden}
                    type="file"
                    aria-label={`${t("features.data.uploadFile")} ${label}`}
                    disabled={uploading === field.key}
                    onChange={(event) => {
                      const picked = event.target.files?.[0];
                      event.target.value = "";
                      if (picked) void handleUpload(field.key, picked);
                    }}
                  />
                </label>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
