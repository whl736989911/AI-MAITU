import { useEffect, useMemo, useState } from "react";
import { Alert, Spin, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  knowledgeBasesApi,
  type KnowledgeProcessors,
} from "../../../api/modules/knowledgeBases";

interface KnowledgeProcessorNoticeProps {
  files: File[];
}

export default function KnowledgeProcessorNotice({
  files,
}: KnowledgeProcessorNoticeProps) {
  const { t } = useTranslation();
  const [capability, setCapability] = useState<KnowledgeProcessors | null>(
    null,
  );
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void knowledgeBasesApi.getProcessors().then(
      (value) => {
        if (!cancelled) setCapability(value);
      },
      () => {
        if (!cancelled) setFailed(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const availableExtensions = useMemo(
    () =>
      new Set(
        (capability?.processors ?? [])
          .filter((processor) => processor.available)
          .flatMap((processor) => processor.extensions)
          .map((extension) => extension.toLowerCase()),
      ),
    [capability],
  );
  const archiveFiles = capability
    ? files.filter((file) => {
        const dot = file.name.lastIndexOf(".");
        return !availableExtensions.has(
          dot < 0 ? "" : file.name.slice(dot).toLowerCase(),
        );
      })
    : [];

  return (
    <div style={{ display: "grid", gap: 8, marginTop: 16 }}>
      <Alert
        type="info"
        showIcon
        message={t("knowledgeBases.processingStorageHint")}
      />
      {failed ? (
        <Alert
          type="warning"
          showIcon
          message={t("knowledgeBases.processingCapabilityFailed")}
        />
      ) : capability ? (
        <>
          <Typography.Text type="secondary">
            {t("knowledgeBases.processingAvailableFormats", {
              formats:
                [...availableExtensions].sort().join(", ") ||
                t("knowledgeBases.processingNoFormats"),
            })}
          </Typography.Text>
          {archiveFiles.length > 0 ? (
            <Alert
              type="warning"
              showIcon
              message={t("knowledgeBases.processingArchiveFiles", {
                count: archiveFiles.length,
              })}
              description={archiveFiles.map((file) => file.name).join(", ")}
            />
          ) : null}
        </>
      ) : (
        <Spin size="small" />
      )}
    </div>
  );
}
