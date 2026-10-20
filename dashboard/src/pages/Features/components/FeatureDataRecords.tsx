/**
 * The records of one object: search, paging, and the way into a record.
 *
 * Reads are server-paged (``limit``/``offset``), so a large ledger does not arrive
 * in one response. Creating a record is allowed for a feature manager here; a
 * caller who may only read sees the same table without the create button, and the
 * server owns the final verdict on every write.
 */

import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Empty, Input, Spin, Table, Tag } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";

import { message } from "@/utils/antdMessage";

import type {
  FeatureDataObject,
  FeatureDataRecord,
} from "../../../api/modules/featureData";
import { featureDataApi } from "../../../api/modules/featureData";
import { apiErrorMessage } from "../../../utils/apiError";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import FeatureDataRecordDetail from "./FeatureDataRecordDetail";
import FeatureDataRecordFields from "./FeatureDataRecordFields";
import {
  buildValuesPayload,
  missingRequiredFields,
  objectFields,
  objectLabel,
  recordTitle,
} from "../utils/featureData";
import styles from "./FeatureDataPanel.module.less";
import { Modal } from "antd";

const PAGE_SIZE = 10;

export interface FeatureDataRecordsProps {
  agentId: string;
  object: FeatureDataObject;
  canWrite: boolean;
}

export default function FeatureDataRecords({
  agentId,
  object,
  canWrite,
}: FeatureDataRecordsProps) {
  const { t, i18n } = useTranslation();
  const language = i18n.language ?? "en";
  const timeZone = useServerTimezone();
  const [records, setRecords] = useState<FeatureDataRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<unknown>(null);
  const [selected, setSelected] = useState<FeatureDataRecord | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [draftValues, setDraftValues] = useState<Record<string, unknown>>({});
  const [draftMissing, setDraftMissing] = useState<readonly string[]>([]);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setFailure(null);
    try {
      const answer = await featureDataApi.listRecords(agentId, object.key, {
        q: query,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      setRecords(answer.records ?? []);
      setTotal(answer.total ?? 0);
    } catch (error) {
      setFailure(error);
    } finally {
      setLoading(false);
    }
  }, [agentId, object.key, page, query]);

  useEffect(() => {
    void load();
  }, [load]);

  // A new object starts at its first page with no search.
  useEffect(() => {
    setPage(1);
    setQuery("");
    setSearch("");
    setSelected(null);
  }, [object.key]);

  const handleOpen = async (record: FeatureDataRecord) => {
    try {
      const fresh = await featureDataApi.getRecord(agentId, record.id);
      setSelected(fresh);
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("features.data.recordLoadFailed"), t),
      );
    }
  };

  const handleCreate = async () => {
    const values = buildValuesPayload(object, draftValues);
    const missing = missingRequiredFields(object, values);
    setDraftMissing(missing);
    if (missing.length > 0) return;
    setCreating(true);
    try {
      const created = await featureDataApi.createRecord(
        agentId,
        object.key,
        values,
      );
      message.success(t("features.data.recordCreated"));
      setCreateOpen(false);
      setDraftValues({});
      setDraftMissing([]);
      setPage(1);
      setQuery("");
      setSearch("");
      setSelected(created);
      await load();
    } catch (error) {
      message.error(apiErrorMessage(error, t("features.data.createFailed"), t));
    } finally {
      setCreating(false);
    }
  };

  if (selected) {
    return (
      <FeatureDataRecordDetail
        agentId={agentId}
        object={object}
        record={selected}
        canWrite={canWrite}
        onBack={() => {
          setSelected(null);
          void load();
        }}
        onDeleted={() => {
          setSelected(null);
          void load();
        }}
      />
    );
  }

  const columns = [
    {
      title: t("features.data.columns.title"),
      dataIndex: "title",
      key: "title",
      render: (_: unknown, record: FeatureDataRecord) => (
        <span className={styles.recordTitleCell}>{recordTitle(record)}</span>
      ),
    },
    {
      title: t("features.data.columns.visibility"),
      dataIndex: "visibility",
      key: "visibility",
      width: 120,
      render: (visibility: unknown) =>
        visibility === "feature" ? (
          <Tag color="green" bordered={false}>
            {t("features.data.visibilityFeature")}
          </Tag>
        ) : (
          <Tag bordered={false}>{t("features.data.visibilityPrivate")}</Tag>
        ),
    },
    {
      title: t("features.data.columns.revision"),
      dataIndex: "revision",
      key: "revision",
      width: 100,
      render: (revision: unknown) => `r${String(revision ?? "")}`,
    },
    {
      title: t("features.data.columns.updatedAt"),
      dataIndex: "updated_at",
      key: "updated_at",
      width: 180,
      render: (value: unknown) =>
        formatServerDateTime(Number(value) || 0, timeZone),
    },
  ];

  return (
    <div className={styles.records}>
      <div className={styles.toolbar}>
        <Input.Search
          allowClear
          className={styles.search}
          placeholder={t("features.data.searchPlaceholder")}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            if (!event.target.value) {
              setQuery("");
              setPage(1);
            }
          }}
          onSearch={(value) => {
            setQuery(value.trim());
            setPage(1);
          }}
        />
        {canWrite ? (
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setDraftValues({});
              setDraftMissing([]);
              setCreateOpen(true);
            }}
          >
            {t("features.data.newRecord")}
          </Button>
        ) : null}
      </div>

      {failure !== null ? (
        <Alert
          type="error"
          showIcon
          message={t("features.data.recordsLoadFailed")}
          description={apiErrorMessage(
            failure,
            t("features.data.recordsLoadFailed"),
            t,
          )}
        />
      ) : (
        <Table<FeatureDataRecord>
          className={styles.table}
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={records}
          columns={columns}
          scroll={{ x: true }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={t("features.data.recordsEmpty")}
              />
            ),
          }}
          onRow={(record) => ({
            onClick: () => void handleOpen(record),
            style: { cursor: "pointer" },
          })}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            showSizeChanger: false,
            onChange: (nextPage) => setPage(nextPage),
          }}
        />
      )}

      {loading && records.length === 0 && failure === null ? (
        <div className={styles.loading}>
          <Spin />
        </div>
      ) : null}

      <Modal
        open={createOpen}
        title={`${t("features.data.newRecord")} · ${objectLabel(
          object,
          language,
        )}`}
        okText={t("common.create")}
        cancelText={t("common.cancel")}
        confirmLoading={creating}
        onOk={() => void handleCreate()}
        onCancel={() => setCreateOpen(false)}
        destroyOnHidden
      >
        <FeatureDataRecordFields
          object={object}
          values={draftValues}
          missing={draftMissing}
          onChange={(key, value) =>
            setDraftValues((previous) => ({ ...previous, [key]: value }))
          }
        />
        {objectFields(object).some((field) => field.type === "file") ? (
          <p className={styles.hint}>{t("features.data.fileAfterCreate")}</p>
        ) : null}
      </Modal>
    </div>
  );
}
