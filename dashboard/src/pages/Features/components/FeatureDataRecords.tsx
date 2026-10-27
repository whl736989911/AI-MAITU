/**
 * The records of one object: search, paging, and the way into a record.
 *
 * Reads are server-paged (``limit``/``offset``), so a large ledger does not arrive
 * in one response. Creating a record is allowed for a feature manager here; a
 * caller who may only read sees the same table without the create button, and the
 * server owns the final verdict on every write.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Input,
  Spin,
  Table,
  Tag,
  Select,
  Space,
  Modal,
} from "antd";
import { useTranslation } from "react-i18next";
import { PlusOutlined } from "@ant-design/icons";

import { message } from "@/utils/antdMessage";

import type {
  FeatureDataFilter,
  FeatureDataObject,
  FeatureDataPrincipal,
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
const PAGE_SIZE = 10;

export interface FeatureDataRecordsProps {
  agentId: string;
  object: FeatureDataObject;
  canWrite: boolean;
  canManage: boolean;
}

export default function FeatureDataRecords({
  agentId,
  object,
  canWrite,
  canManage,
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
  const [filters, setFilters] = useState<FeatureDataFilter[]>([]);
  const [sortBy, setSortBy] = useState("updated_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const requestId = useRef(0);
  const activeObjectKey = useRef(object.key);
  activeObjectKey.current = object.key;
  const appliedFilters = useMemo(
    () =>
      filters.filter((filter) => {
        if (filter.op === "is_empty") return true;
        const value = filter.value;
        if (value === undefined || value === null || value === "") return false;
        if (typeof value === "number" && !Number.isFinite(value)) return false;
        if (
          Array.isArray(value) &&
          (value.length === 0 ||
            value.some(
              (item) =>
                item === "" ||
                (typeof item === "number" && !Number.isFinite(item)),
            ))
        )
          return false;
        return true;
      }),
    [filters],
  );
  useEffect(() => {
    setPage(1);
  }, [filters, sortBy, sortOrder]);
  const [handoverOpen, setHandoverOpen] = useState(false);
  const [handoverFrom, setHandoverFrom] = useState<number>();
  const [handoverTo, setHandoverTo] = useState<number>();
  const [handoverUsers, setHandoverUsers] = useState<FeatureDataPrincipal[]>(
    [],
  );
  const [handingOver, setHandingOver] = useState(false);

  const load = useCallback(async () => {
    const id = ++requestId.current;
    const requestedObject = object.key;
    setLoading(true);
    setFailure(null);
    try {
      const answer = await featureDataApi.listRecords(
        agentId,
        requestedObject,
        {
          q: query,
          limit: PAGE_SIZE,
          offset: (page - 1) * PAGE_SIZE,
          filters: appliedFilters,
          sort_by: sortBy,
          sort_order: sortOrder,
        },
      );
      if (
        id !== requestId.current ||
        activeObjectKey.current !== requestedObject
      )
        return;
      setRecords(answer.records ?? []);
      setTotal(answer.total ?? 0);
    } catch (error) {
      if (
        id !== requestId.current ||
        activeObjectKey.current !== requestedObject
      )
        return;
      setFailure(error);
    } finally {
      if (
        id === requestId.current &&
        activeObjectKey.current === requestedObject
      )
        setLoading(false);
    }
  }, [agentId, object.key, page, query, appliedFilters, sortBy, sortOrder]);

  useEffect(() => {
    void load();
    return () => {
      requestId.current += 1;
    };
  }, [load]);

  // A new object starts at its first page with no search.
  useEffect(() => {
    setPage(1);
    setQuery("");
    setSearch("");
    setSelected(null);
    setFilters([]);
  }, [object.key]);

  const handleOpen = async (record: FeatureDataRecord) => {
    const requestedObject = object.key;
    try {
      const fresh = await featureDataApi.getRecord(agentId, record.id);
      if (activeObjectKey.current === requestedObject) setSelected(fresh);
    } catch (error) {
      if (activeObjectKey.current !== requestedObject) return;
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
  const submitHandover = async () => {
    if (!canManage || handoverFrom === undefined || handoverTo === undefined)
      return;
    setHandingOver(true);
    try {
      const result = await featureDataApi.handover(agentId, {
        from_user_id: handoverFrom,
        to_user_id: handoverTo,
        object_key: object.key,
      });
      message.success(
        t("features.data.handoverSuccess", { count: result.transferred_count }),
      );
      setHandoverOpen(false);
      await load();
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("features.data.handoverFailed"), t),
      );
    } finally {
      setHandingOver(false);
    }
  };

  const handoverSourceOptions = useMemo(() => {
    const choices = new Map(
      handoverUsers.map((user) => [
        user.id,
        user.display_name || user.username,
      ]),
    );
    for (const record of records) {
      if (record.visibility !== "feature") continue;
      const id = record.responsible_user_id ?? record.created_by;
      if (!choices.has(id)) {
        choices.set(id, t("features.data.recordResponsibleId", { id }));
      }
    }
    return [...choices].map(([value, label]) => ({ value, label }));
  }, [handoverUsers, records, t]);

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
        {canManage ? (
          <Button
            onClick={() => {
              setHandoverFrom(undefined);
              setHandoverTo(undefined);
              void featureDataApi
                .listPrincipals(agentId, { limit: 200 })
                .then((answer) => {
                  setHandoverUsers(answer.principals ?? []);
                  setHandoverOpen(true);
                })
                .catch((error) =>
                  message.error(
                    apiErrorMessage(
                      error,
                      t("features.data.handoverFailed"),
                      t,
                    ),
                  ),
                );
            }}
          >
            {t("features.data.handover")}
          </Button>
        ) : null}
      </div>
      <Space wrap style={{ margin: "8px 0", width: "100%" }}>
        <Select
          aria-label={t("features.data.sortBy")}
          value={sortBy}
          style={{ minWidth: 170 }}
          options={[
            { value: "created_at", label: t("features.data.sortCreated") },
            { value: "updated_at", label: t("features.data.sortUpdated") },
            { value: "title", label: t("features.data.sortTitle") },
            ...objectFields(object).map((field) => ({
              value: field.key,
              label: field.key,
            })),
          ]}
          onChange={(value) => {
            setSortBy(value);
            setPage(1);
          }}
        />
        <Select
          aria-label={t("features.data.sortOrder")}
          value={sortOrder}
          options={[
            { value: "asc", label: t("features.data.ascending") },
            { value: "desc", label: t("features.data.descending") },
          ]}
          onChange={(value) => {
            setSortOrder(value);
            setPage(1);
          }}
        />
        <Button
          disabled={filters.length >= 20}
          onClick={() => {
            const field = objectFields(object)[0];
            if (field)
              setFilters((current) => [
                ...current,
                { field: field.key, op: "eq", value: "" },
              ]);
          }}
        >
          {t("features.data.addFilter")}
        </Button>
        {filters.map((filter, index) => {
          const field =
            objectFields(object).find((item) => item.key === filter.field) ??
            objectFields(object)[0];
          return (
            <Space key={`${index}-${filter.field}`} size="small">
              <Select
                aria-label={t("features.data.filterField")}
                value={filter.field}
                options={objectFields(object).map((item) => ({
                  value: item.key,
                  label: item.key,
                }))}
                onChange={(fieldKey) =>
                  setFilters((current) =>
                    current.map((item, at) =>
                      at === index ? { ...item, field: fieldKey } : item,
                    ),
                  )
                }
              />
              <Select
                aria-label={t("features.data.filterOperator")}
                value={filter.op}
                options={(field?.type === "number" ||
                field?.type === "integer" ||
                field?.type === "date" ||
                field?.type === "datetime"
                  ? ["eq", "ne", "gt", "gte", "lt", "lte", "in", "is_empty"]
                  : field?.type === "boolean"
                  ? ["eq", "ne", "is_empty"]
                  : ["eq", "ne", "contains", "in", "is_empty"]
                ).map((op) => ({
                  value: op,
                  label: t(`features.data.filterOps.${op}`),
                }))}
                onChange={(op) =>
                  setFilters((current) =>
                    current.map((item, at) =>
                      at === index
                        ? {
                            ...item,
                            op: op as FeatureDataFilter["op"],
                            value:
                              op === "is_empty" ? undefined : item.value ?? "",
                          }
                        : item,
                    ),
                  )
                }
              />
              {filter.op === "is_empty" ? null : field?.type === "boolean" ? (
                <Select
                  aria-label={t("features.data.filterValue")}
                  value={
                    filter.value === undefined
                      ? undefined
                      : String(filter.value)
                  }
                  options={[
                    { value: "true", label: t("features.data.yes") },
                    { value: "false", label: t("features.data.no") },
                  ]}
                  onChange={(value) =>
                    setFilters((current) =>
                      current.map((item, at) =>
                        at === index
                          ? { ...item, value: value === "true" }
                          : item,
                      ),
                    )
                  }
                />
              ) : (
                <Input
                  aria-label={t("features.data.filterValue")}
                  type={
                    filter.op === "in"
                      ? "text"
                      : field?.type === "integer" || field?.type === "number"
                      ? "number"
                      : field?.type === "date"
                      ? "date"
                      : field?.type === "datetime"
                      ? "datetime-local"
                      : "text"
                  }
                  placeholder={
                    filter.op === "in" ? "value1, value2" : undefined
                  }
                  value={
                    filter.op === "in" && Array.isArray(filter.value)
                      ? filter.value.join(", ")
                      : String(filter.value ?? "")
                  }
                  onChange={(event) => {
                    const raw = event.target.value;
                    const parseValue = (part: string) => {
                      const trimmed = part.trim();
                      if (!trimmed) return "";
                      return field?.type === "number" ||
                        field?.type === "integer"
                        ? Number(trimmed)
                        : trimmed;
                    };
                    const value =
                      filter.op === "in"
                        ? raw
                            .split(",")
                            .map(parseValue)
                            .filter((item) => item !== "")
                        : field?.type === "number" || field?.type === "integer"
                        ? raw === ""
                          ? ""
                          : Number(raw)
                        : raw;
                    setFilters((current) =>
                      current.map((item, at) =>
                        at === index ? { ...item, value } : item,
                      ),
                    );
                  }}
                />
              )}
              <Button
                danger
                onClick={() =>
                  setFilters((current) =>
                    current.filter((_, at) => at !== index),
                  )
                }
              >
                {t("features.data.removeFilter")}
              </Button>
            </Space>
          );
        })}
      </Space>

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
          agentId={agentId}
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
      <Modal
        open={handoverOpen && canManage}
        title={t("features.data.handover")}
        okText={t("features.data.handover")}
        cancelText={t("common.cancel")}
        confirmLoading={handingOver}
        okButtonProps={{
          disabled:
            handoverFrom === undefined ||
            handoverTo === undefined ||
            handoverFrom === handoverTo,
        }}
        onOk={() => void submitHandover()}
        onCancel={() => setHandoverOpen(false)}
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Select
            aria-label={t("features.data.handoverFrom")}
            placeholder={t("features.data.handoverFrom")}
            showSearch
            filterOption={false}
            value={handoverFrom}
            options={handoverSourceOptions}
            onSearch={(q) => {
              void featureDataApi
                .listPrincipals(agentId, { q, limit: 200 })
                .then((answer) => setHandoverUsers(answer.principals ?? []))
                .catch((error) =>
                  message.error(
                    apiErrorMessage(
                      error,
                      t("features.data.handoverFailed"),
                      t,
                    ),
                  ),
                );
            }}
            onChange={setHandoverFrom}
          />
          <Select
            aria-label={t("features.data.handoverTo")}
            placeholder={t("features.data.handoverTo")}
            showSearch
            filterOption={false}
            value={handoverTo}
            options={handoverUsers
              .filter((user) => user.id !== handoverFrom)
              .map((user) => ({
                value: user.id,
                label: user.display_name || user.username,
              }))}
            onSearch={(q) => {
              void featureDataApi
                .listPrincipals(agentId, { q, limit: 200 })
                .then((answer) => setHandoverUsers(answer.principals ?? []))
                .catch((error) =>
                  message.error(
                    apiErrorMessage(
                      error,
                      t("features.data.handoverFailed"),
                      t,
                    ),
                  ),
                );
            }}
            onChange={setHandoverTo}
          />
        </Space>
      </Modal>
    </div>
  );
}
