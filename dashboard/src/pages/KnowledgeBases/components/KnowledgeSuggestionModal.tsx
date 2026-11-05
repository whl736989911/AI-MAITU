import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { Check, RefreshCw, Sparkles, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  knowledgeBasesApi,
  type KnowledgeDocument,
  type KnowledgeDocumentSuggestion,
  type KnowledgeMetadataField,
} from "../../../api/modules/knowledgeBases";
import { apiErrorMessage } from "../../../utils/apiError";

interface Props {
  open: boolean;
  baseId: string;
  schema: KnowledgeMetadataField[];
  document: KnowledgeDocument | null;
  onClose: () => void;
  onSaved?: () => void;
}

/** Review AI summaries, keywords and field values before they touch a document. */
export default function KnowledgeSuggestionModal({
  open,
  baseId,
  schema,
  document: target,
  onClose,
  onSaved,
}: Props) {
  const { t } = useTranslation();
  const [suggestion, setSuggestion] =
    useState<KnowledgeDocumentSuggestion | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [error, setError] = useState("");
  // Every load/generate owns a sequence number. Closing the dialog or opening
  // another document invalidates older requests so their response cannot
  // replace the suggestion now on screen.
  const requestRef = useRef(0);

  const adopt = useCallback((next: KnowledgeDocumentSuggestion | null) => {
    setSuggestion(next);
    setValues({ ...(next?.fields ?? {}) });
  }, []);

  const loadPending = useCallback(
    async (requestId: number, documentId: string) => {
      setLoading(true);
      setError("");
      try {
        const rows = await knowledgeBasesApi.listSuggestions(baseId, {
          documentId,
          status: "pending",
          limit: 1,
        });
        if (requestId !== requestRef.current) return;
        adopt(rows[0]?.document_id === documentId ? rows[0] : null);
      } catch (err) {
        if (requestId !== requestRef.current) return;
        setError(
          apiErrorMessage(err, t("knowledgeBases.suggestions.loadFailed"), t),
        );
        adopt(null);
      } finally {
        if (requestId === requestRef.current) setLoading(false);
      }
    },
    [adopt, baseId, t],
  );

  useEffect(() => {
    const requestId = ++requestRef.current;
    if (!open || !target) return;
    adopt(null);
    setError("");
    void loadPending(requestId, target.id);
    return () => {
      if (requestRef.current === requestId) requestRef.current += 1;
    };
  }, [adopt, loadPending, open, target]);

  const generate = async () => {
    if (!target) return;
    const requestId = ++requestRef.current;
    const documentId = target.id;
    setLoading(false);
    setGenerating(true);
    setError("");
    try {
      const next = await knowledgeBasesApi.enrichDocument(baseId, documentId);
      if (requestId !== requestRef.current || next.document_id !== documentId) {
        return;
      }
      adopt(next);
    } catch (err) {
      if (requestId !== requestRef.current) return;
      setError(
        apiErrorMessage(err, t("knowledgeBases.suggestions.generateFailed"), t),
      );
    } finally {
      if (requestId === requestRef.current) setGenerating(false);
    }
  };

  const apply = async () => {
    if (!suggestion) return;
    setDeciding(true);
    try {
      await knowledgeBasesApi.applySuggestion(
        baseId,
        suggestion.suggestion_id,
        values,
      );
      message.success(t("knowledgeBases.suggestions.applySuccess"));
      onSaved?.();
      onClose();
    } catch (err) {
      message.error(
        apiErrorMessage(err, t("knowledgeBases.suggestions.applyFailed"), t),
      );
    } finally {
      setDeciding(false);
    }
  };

  const reject = async () => {
    if (!suggestion) return;
    setDeciding(true);
    try {
      await knowledgeBasesApi.rejectSuggestion(
        baseId,
        suggestion.suggestion_id,
      );
      message.success(t("knowledgeBases.suggestions.rejectSuccess"));
      adopt(null);
    } catch (err) {
      message.error(
        apiErrorMessage(err, t("knowledgeBases.suggestions.rejectFailed"), t),
      );
    } finally {
      setDeciding(false);
    }
  };

  const updateValue = (field: KnowledgeMetadataField, raw: string) => {
    let value: unknown = raw;
    if (field.type === "number") value = raw === "" ? "" : Number(raw);
    setValues((current) => ({ ...current, [field.key]: value }));
  };

  return (
    <Modal
      open={open}
      title={
        <Space size={6}>
          <Sparkles size={16} />
          {t("knowledgeBases.suggestions.title")}
        </Space>
      }
      onCancel={onClose}
      width={620}
      footer={
        suggestion ? (
          <Space>
            <Button
              icon={<X size={14} />}
              disabled={deciding}
              onClick={() => void reject()}
            >
              {t("knowledgeBases.suggestions.reject")}
            </Button>
            <Button
              type="primary"
              icon={<Check size={14} />}
              loading={deciding}
              onClick={() => void apply()}
            >
              {t("knowledgeBases.suggestions.apply")}
            </Button>
          </Space>
        ) : null
      }
    >
      <Spin spinning={loading}>
        <Space direction="vertical" style={{ width: "100%" }} size={14}>
          <Typography.Text type="secondary">{target?.filename}</Typography.Text>
          {error ? <Alert type="error" showIcon message={error} /> : null}
          {loading ? null : suggestion ? (
            <>
              <div>
                <Typography.Text strong>
                  {t("knowledgeBases.suggestions.summary")}
                </Typography.Text>
                <Typography.Paragraph style={{ margin: "4px 0 0" }}>
                  {suggestion.summary ||
                    t("knowledgeBases.suggestions.emptySummary")}
                </Typography.Paragraph>
              </div>
              <div>
                <Typography.Text strong>
                  {t("knowledgeBases.suggestions.keywords")}
                </Typography.Text>
                <div style={{ marginTop: 4 }}>
                  {suggestion.keywords.length === 0 ? (
                    <Typography.Text type="secondary">
                      {t("knowledgeBases.suggestions.emptyKeywords")}
                    </Typography.Text>
                  ) : (
                    suggestion.keywords.map((keyword) => (
                      <Tag key={keyword}>{keyword}</Tag>
                    ))
                  )}
                </div>
              </div>
              {schema.length > 0 ? (
                <div>
                  <Typography.Text strong>
                    {t("knowledgeBases.suggestions.fields")}
                  </Typography.Text>
                  <Space
                    direction="vertical"
                    style={{ width: "100%", marginTop: 6 }}
                    size={10}
                  >
                    {schema.map((field) => (
                      <label key={field.key} style={{ display: "block" }}>
                        <Typography.Text>
                          {field.label || field.key}
                        </Typography.Text>
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
                            onChange={(event) =>
                              updateValue(field, event.target.value)
                            }
                          />
                        )}
                      </label>
                    ))}
                  </Space>
                </div>
              ) : null}
              <Button
                size="small"
                icon={<RefreshCw size={14} />}
                loading={generating}
                onClick={() => void generate()}
              >
                {t("knowledgeBases.suggestions.regenerate")}
              </Button>
            </>
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t("knowledgeBases.suggestions.empty")}
            >
              <Button
                type="primary"
                icon={<Sparkles size={14} />}
                loading={generating}
                onClick={() => void generate()}
              >
                {t("knowledgeBases.suggestions.generate")}
              </Button>
            </Empty>
          )}
        </Space>
      </Spin>
    </Modal>
  );
}
