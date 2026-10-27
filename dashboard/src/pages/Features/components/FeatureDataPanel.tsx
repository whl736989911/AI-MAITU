/**
 * 业务数据 — a feature's own business objects, records and reminders.
 *
 * The panel is the drawer's second view: objects on the left, the selected
 * object's records on the right, plus the caller's notifications and — for a
 * feature manager — the schema document that defines both.
 *
 * Two rules shape the permission split, and both come from the caller:
 *   - reading objects and records is for anyone who may reach the feature;
 *   - writing the schema (and, in this MVP, changing records) is for a feature
 *     manager. Nothing here derives a role of its own; ``canWrite`` is the only
 *     verdict consulted, so the drawer and the server cannot disagree about who
 *     may configure the feature.
 *
 * A schema that cannot be read is said, not hidden: the manager's editor shows
 * the stored error and starts from an empty document, because a broken file is
 * not a document this build can offer to repair in place.
 */

import { useEffect, useMemo, useState } from "react";
import { Alert, Empty, List, Spin, Tag, Tabs } from "antd";
import { useTranslation } from "react-i18next";

import type { FeatureDataSchema } from "../../../api/modules/featureData";
import { featureDataApi } from "../../../api/modules/featureData";
import { apiErrorMessage } from "../../../utils/apiError";
import FeatureDataNotifications from "./FeatureDataNotifications";
import FeatureDataRecords from "./FeatureDataRecords";
import FeatureDataSchemaEditor from "./FeatureDataSchemaEditor";
import { objectLabel, schemaObjects } from "../utils/featureData";
import styles from "./FeatureDataPanel.module.less";

export interface FeatureDataPanelProps {
  /** The feature's agent id — both schema and records live on that path. */
  agentId: string;
  /** Whether this caller may configure the feature (its managers). */
  canWrite: boolean;
  /** Whether this caller may create/edit records; the server still checks ownership. */
  canEditRecords?: boolean;
}

type DataTab = "records" | "notifications" | "schema";

export default function FeatureDataPanel({
  agentId,
  canWrite,
  canEditRecords = canWrite,
}: FeatureDataPanelProps) {
  const { t, i18n } = useTranslation();
  const language = i18n.language ?? "en";
  const [loading, setLoading] = useState(true);
  const [loadFailure, setLoadFailure] = useState<unknown>(null);
  const [schema, setSchema] = useState<FeatureDataSchema | null>(null);
  const [storedError, setStoredError] = useState<string | null>(null);
  const [objectKey, setObjectKey] = useState<string>("");
  const [tab, setTab] = useState<DataTab>("records");

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setLoadFailure(null);
      try {
        const answer = await featureDataApi.getSchema(agentId);
        if (cancelled) return;
        setSchema(answer.schema ?? null);
        setStoredError(answer.error ?? null);
        const first = schemaObjects(answer.schema)[0];
        setObjectKey((previous) => previous || first?.key || "");
      } catch (error) {
        if (!cancelled) setLoadFailure(error);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  const objects = useMemo(() => schemaObjects(schema), [schema]);
  const selectedObject = useMemo(
    () =>
      objects.find((object) => object.key === objectKey) ?? objects[0] ?? null,
    [objectKey, objects],
  );

  if (loading) {
    return (
      <div className={styles.loading}>
        <Spin />
      </div>
    );
  }

  if (loadFailure !== null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("features.data.loadFailed")}
        description={apiErrorMessage(
          loadFailure,
          t("features.data.loadFailed"),
          t,
        )}
      />
    );
  }

  const tabItems = [
    {
      key: "records",
      label: t("features.data.tabRecords"),
      children: (
        <div className={styles.recordsTab}>
          {objects.length === 0 ? (
            <Empty
              className={styles.empty}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <div className={styles.emptyText}>
                  <span>{t("features.data.objectsEmpty")}</span>
                  <span className={styles.hint}>
                    {canWrite
                      ? t("features.data.objectsEmptyManager")
                      : t("features.data.objectsEmptyHint")}
                  </span>
                </div>
              }
            />
          ) : (
            <div className={styles.layout}>
              <List
                className={styles.objectList}
                size="small"
                dataSource={objects}
                rowKey={(object) => object.key}
                locale={{ emptyText: t("features.data.objectsEmpty") }}
                renderItem={(object) => (
                  <List.Item
                    className={
                      object.key === selectedObject?.key
                        ? styles.objectRowActive
                        : styles.objectRow
                    }
                    onClick={() => setObjectKey(object.key)}
                  >
                    <div className={styles.objectMain}>
                      <span className={styles.objectName}>
                        {objectLabel(object, language)}
                      </span>
                      <span className={styles.objectMeta}>
                        <Tag bordered={false}>
                          {object.kind === "document"
                            ? t("features.data.kindDocument")
                            : t("features.data.kindLedger")}
                        </Tag>
                        {object.controlled ? (
                          <Tag color="orange" bordered={false}>
                            {t("features.data.controlled")}
                          </Tag>
                        ) : null}
                      </span>
                    </div>
                  </List.Item>
                )}
              />
              <div className={styles.recordsPane}>
                {selectedObject ? (
                  <FeatureDataRecords
                    key={selectedObject.key}
                    agentId={agentId}
                    object={selectedObject}
                    canWrite={canEditRecords}
                    canManage={canWrite}
                  />
                ) : null}
              </div>
            </div>
          )}
        </div>
      ),
    },
    {
      key: "notifications",
      label: t("features.data.tabNotifications"),
      children: <FeatureDataNotifications agentId={agentId} />,
    },
  ];
  if (canWrite) {
    tabItems.push({
      key: "schema",
      label: t("features.data.tabSchema"),
      children: (
        <FeatureDataSchemaEditor
          agentId={agentId}
          schema={schema}
          storedError={storedError}
          onSaved={(next) => {
            setSchema(next);
            setStoredError(null);
            const first = schemaObjects(next)[0];
            setObjectKey((previous) =>
              previous &&
              schemaObjects(next).some((object) => object.key === previous)
                ? previous
                : first?.key || "",
            );
          }}
        />
      ),
    });
  }

  return (
    <div className={styles.panel}>
      <Tabs
        activeKey={tab}
        onChange={(key) => setTab(key as DataTab)}
        items={tabItems}
        destroyOnHidden={false}
      />
    </div>
  );
}
