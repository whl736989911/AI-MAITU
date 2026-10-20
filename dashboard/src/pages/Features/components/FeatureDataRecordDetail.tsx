/**
 * One record: its fields, its attachments, its history, and the actions that
 * change its state.
 *
 * The editor keeps a local copy of ``values`` so typing does not rewrite the
 * server's record before it is saved; the save sends that copy with the
 * ``revision`` the record was read at, and the answer replaces both. A controlled
 * document additionally states its version, status and revision at the top: that
 * is the identity a reader checks before trusting the paper.
 */

import { useMemo, useState } from "react";
import { Alert, Button, Popconfirm, Space, Tag } from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";

import { message } from "@/utils/antdMessage";

import type {
  FeatureDataObject,
  FeatureDataRecord,
} from "../../../api/modules/featureData";
import { featureDataApi } from "../../../api/modules/featureData";
import { apiErrorMessage, parseApiError } from "../../../utils/apiError";
import {
  buildValuesPayload,
  displayFieldValue,
  fieldLabel,
  isControlled,
  missingRequiredFields,
  objectFields,
  recordTitle,
  valueFields,
} from "../utils/featureData";
import FeatureDataAttachments from "./FeatureDataAttachments";
import FeatureDataHistory from "./FeatureDataHistory";
import FeatureDataRecordFields from "./FeatureDataRecordFields";
import styles from "./FeatureDataPanel.module.less";

export interface FeatureDataRecordDetailProps {
  agentId: string;
  object: FeatureDataObject;
  record: FeatureDataRecord;
  canWrite: boolean;
  onBack: () => void;
  /** The record was deleted: leave the detail view and reload the list. */
  onDeleted: () => void;
}

export default function FeatureDataRecordDetail({
  agentId,
  object,
  record,
  canWrite,
  onBack,
  onDeleted,
}: FeatureDataRecordDetailProps) {
  const { t, i18n } = useTranslation();
  const language = i18n.language ?? "en";
  const [current, setCurrent] = useState(record);
  const [values, setValues] = useState<Record<string, unknown>>({
    ...record.values,
  });
  const [missing, setMissing] = useState<readonly string[]>([]);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);

  const fields = objectFields(object);
  const editableFields = valueFields(object);
  const controlled = isControlled(object);
  const versionField = fields.find((field) => field.key === "version");
  const statusField = fields.find((field) => field.key === "status");

  const statusValue = statusField ? current.values[statusField.key] : undefined;
  const versionValue = versionField
    ? current.values[versionField.key]
    : undefined;
  const revisionReason = fields.find(
    (field) => field.key === "revision_reason",
  );
  const revisionReasonValue = revisionReason
    ? current.values[revisionReason.key]
    : undefined;

  const metaTags = useMemo(() => {
    const tags: { key: string; label: string }[] = [
      {
        key: "revision",
        label: t("features.data.metaRevision", {
          revision: current.revision,
        }),
      },
    ];
    if (typeof versionValue === "string" && versionValue.trim()) {
      tags.push({
        key: "version",
        label: `${
          versionField ? fieldLabel(versionField, language) : "version"
        }: ${versionValue}`,
      });
    }
    if (typeof statusValue === "string" && statusValue.trim()) {
      tags.push({
        key: "status",
        label: `${
          statusField ? fieldLabel(statusField, language) : "status"
        }: ${statusValue}`,
      });
    }
    return tags;
  }, [
    current.revision,
    language,
    statusField,
    statusValue,
    t,
    versionField,
    versionValue,
  ]);

  const reloadRecord = async () => {
    try {
      const fresh = await featureDataApi.getRecord(agentId, current.id);
      setCurrent(fresh);
      setValues({ ...fresh.values });
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("features.data.recordLoadFailed"), t),
      );
    }
  };

  const handleSave = async () => {
    const payload = buildValuesPayload(object, values);
    const blank = missingRequiredFields(object, payload);
    setMissing(blank);
    if (blank.length > 0) return;
    setSaving(true);
    try {
      const updated = await featureDataApi.updateRecord(
        agentId,
        current.id,
        payload,
        current.revision,
      );
      setCurrent(updated);
      setValues({ ...updated.values });
      setMissing([]);
      setHistoryKey((key) => key + 1);
      message.success(t("features.data.recordSaved"));
    } catch (error) {
      const parsed = parseApiError(error);
      if (parsed?.code === "FEATURE_DATA_REVISION_CONFLICT") {
        message.error(t("features.data.revisionConflict"));
      } else {
        message.error(
          apiErrorMessage(error, t("features.data.recordSaveFailed"), t),
        );
      }
    } finally {
      setSaving(false);
    }
  };

  const handlePublish = async () => {
    setPublishing(true);
    try {
      const updated = await featureDataApi.publishRecord(agentId, current.id);
      setCurrent(updated);
      setValues({ ...updated.values });
      setHistoryKey((key) => key + 1);
      message.success(t("features.data.published"));
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("features.data.publishFailed"), t),
      );
    } finally {
      setPublishing(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await featureDataApi.deleteRecord(agentId, current.id);
      message.success(t("features.data.deleted"));
      onDeleted();
    } catch (error) {
      message.error(apiErrorMessage(error, t("features.data.deleteFailed"), t));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className={styles.detail}>
      <div className={styles.detailHeader}>
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={onBack}
          aria-label={t("features.data.backToList")}
        >
          {t("features.data.backToList")}
        </Button>
        <span className={styles.detailTitle}>{recordTitle(current)}</span>
        <Tag
          color={current.visibility === "feature" ? "green" : "default"}
          bordered={false}
        >
          {current.visibility === "feature"
            ? t("features.data.visibilityFeature")
            : t("features.data.visibilityPrivate")}
        </Tag>
        {controlled
          ? metaTags.map((tag) => (
              <Tag key={tag.key} bordered={false}>
                {tag.label}
              </Tag>
            ))
          : null}
      </div>

      {controlled &&
      typeof revisionReasonValue === "string" &&
      revisionReasonValue.trim() ? (
        <Alert
          className={styles.notice}
          type="info"
          showIcon
          message={t("features.data.revisionReason")}
          description={revisionReasonValue}
        />
      ) : null}

      {canWrite ? (
        <>
          <FeatureDataRecordFields
            object={object}
            values={values}
            missing={missing}
            onChange={(key, value) =>
              setValues((previous) => ({ ...previous, [key]: value }))
            }
          />
          <div className={styles.footer}>
            {current.visibility === "private" ? (
              <Popconfirm
                title={t("features.data.publishConfirm")}
                onConfirm={() => void handlePublish()}
              >
                <Button loading={publishing}>
                  {t("features.data.publish")}
                </Button>
              </Popconfirm>
            ) : null}
            <Popconfirm
              title={t("features.data.deleteConfirm")}
              onConfirm={() => void handleDelete()}
            >
              <Button danger loading={deleting}>
                {t("features.data.delete")}
              </Button>
            </Popconfirm>
            <span className={styles.toolbarGap} />
            <Button
              type="primary"
              loading={saving}
              onClick={() => void handleSave()}
            >
              {t("common.save")}
            </Button>
          </div>
        </>
      ) : (
        <div className={styles.fields}>
          {editableFields.map((field) => (
            <div className={styles.field} key={field.key}>
              <span className={styles.fieldLabel}>
                {fieldLabel(field, language)}
              </span>
              <span className={styles.readonlyValue}>
                {displayFieldValue(field, current.values[field.key], {
                  yes: t("features.data.yes"),
                  no: t("features.data.no"),
                }) || t("features.data.emptyValue")}
              </span>
            </div>
          ))}
        </div>
      )}

      <FeatureDataAttachments
        agentId={agentId}
        object={object}
        record={current}
        canWrite={canWrite}
        onChanged={() => void reloadRecord()}
      />

      <div className={styles.section}>
        <span className={styles.sectionTitle}>
          {t("features.data.history")}
        </span>
        <FeatureDataHistory
          agentId={agentId}
          recordId={current.id}
          refreshKey={historyKey}
        />
      </div>

      <Space />
    </div>
  );
}
