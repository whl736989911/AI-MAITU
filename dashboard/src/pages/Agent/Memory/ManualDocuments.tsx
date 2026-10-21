import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Input,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import { useTranslation } from "react-i18next";
import memoryDashboardApi, {
  type ManualMemoryDocument,
  type MemoryScope,
} from "../../../api/modules/memoryDashboard";
import memoryReviewApi, {
  type MemoryReviewConflict,
  type MemoryReviewResult,
} from "../../../api/modules/memoryReview";

interface Props {
  agentId: string;
  scope?: MemoryScope;
  availableScopes?: MemoryScope[];
  readOnly?: boolean;
}

export default function ManualDocuments({
  agentId,
  scope,
  availableScopes = ["shared"],
  readOnly = false,
}: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState<ManualMemoryDocument["name"]>("USER.md");
  const [scopeChoice, setScopeChoice] = useState<MemoryScope>(
    scope ?? "shared",
  );
  const [document, setDocument] = useState<ManualMemoryDocument | null>(null);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [stale, setStale] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [review, setReview] = useState<MemoryReviewResult | null>(null);
  const [selectedFacts, setSelectedFacts] = useState<string[]>([]);

  useEffect(() => setScopeChoice(scope ?? "shared"), [scope]);

  const load = useCallback(async () => {
    setLoading(true);
    setStale(false);
    try {
      const current = await memoryDashboardApi.getManualDocument(
        agentId,
        name,
        scopeChoice,
      );
      setDocument(current);
      setText(current.text);
    } catch {
      setDocument(null);
      message.error(t("memory.manual.loadFailed", "无法加载手动记忆文档"));
    } finally {
      setLoading(false);
    }
  }, [agentId, name, scopeChoice, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    if (!document || !document.editable || readOnly) return;
    setSaving(true);
    try {
      const saved = await memoryDashboardApi.putManualDocument(
        agentId,
        name,
        { text, expected_snapshot: document.snapshot },
        scopeChoice,
      );
      setDocument({ ...document, ...saved });
      setText(saved.text);
      message.success(
        t("memory.manual.saved", "文档已保存；新会话会读取此版本"),
      );
    } catch (error) {
      setStale(true);
      message.error(
        error instanceof Error
          ? error.message
          : t("memory.manual.saveFailed", "保存失败，请重新加载并审阅"),
      );
    } finally {
      setSaving(false);
    }
  };
  const runReview = async () => {
    setReviewing(true);
    setReview(null);
    setSelectedFacts([]);
    try {
      const result = await memoryReviewApi.review(agentId, scopeChoice);
      setReview(result);
      if (result.status === "unavailable") {
        message.warning(
          t("memory.review.unavailable", "语义审查暂不可用，未作任何更改"),
        );
      }
    } catch (error) {
      setReview({
        status: "unavailable",
        reason: "request_failed",
        message: error instanceof Error ? error.message : "",
        scope: scopeChoice,
        coverage: { facts_reviewed: 0, facts_total: 0 },
      });
    } finally {
      setReviewing(false);
    }
  };

  const applyReview = async () => {
    if (!review?.receipt || selectedFacts.length === 0) return;
    setApplying(true);
    try {
      await memoryReviewApi.apply(
        agentId,
        scopeChoice,
        review.receipt,
        selectedFacts,
      );
      message.success(
        t("memory.review.applied", "已保留手动文档，并仅停用所选矛盾事实"),
      );
      setReview(null);
      setSelectedFacts([]);
    } catch (error) {
      message.error(
        error instanceof Error
          ? error.message
          : t("memory.review.stale", "审查已过期或状态已变化；请重新审查"),
      );
    } finally {
      setApplying(false);
    }
  };

  const writable = Boolean(document?.editable && !readOnly);
  const byteLength = new TextEncoder().encode(text).length;
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("memory.manual.title", "手动记忆文档")}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        {t(
          "memory.manual.description",
          "手动内容优先于结构化回忆。更改仅适用于之后开始的新会话。",
        )}
      </Typography.Paragraph>
      <Space wrap>
        <Select
          value={name}
          onChange={setName}
          options={[
            { value: "USER.md", label: "USER.md" },
            { value: "MEMORY.md", label: "MEMORY.md" },
          ]}
        />
        {availableScopes.length > 1 && (
          <Select
            value={scopeChoice}
            onChange={setScopeChoice}
            options={availableScopes.map((value) => ({
              value,
              label:
                value === "private"
                  ? t("memory.manual.private", "我的私有记忆")
                  : t("memory.manual.shared", "共享记忆"),
            }))}
          />
        )}
        {document && (
          <Tag color={document.editable ? "green" : "default"}>
            {document.editable
              ? t("memory.manual.editable", "可编辑")
              : t("memory.manual.readOnly", "只读")}
          </Tag>
        )}
        {document?.revision != null && (
          <Tag>
            {t("memory.manual.version", "版本 {{version}}", {
              version: document.revision,
            })}
          </Tag>
        )}
      </Space>
      {stale && (
        <Alert
          type="warning"
          showIcon
          message={t(
            "memory.manual.stale",
            "文档已被其他编辑更新。请重新加载并审阅当前内容后再编辑。",
          )}
          action={
            <Button size="small" onClick={() => void load()}>
              {t("common.refresh", "重新加载")}
            </Button>
          }
        />
      )}
      <Input.TextArea
        value={text}
        onChange={(event) => setText(event.target.value)}
        readOnly={!writable}
        autoSize={{ minRows: 12, maxRows: 28 }}
      />
      <Space>
        <Typography.Text type="secondary">
          {t("memory.manual.byteCount", "{{used}} / {{max}} 字节", {
            used: byteLength,
            max: document?.max_bytes ?? 65536,
          })}
        </Typography.Text>
        <Button onClick={() => void load()} disabled={loading}>
          {t("common.refresh", "重新加载")}
        </Button>
        <Button
          type="primary"
          onClick={() => void save()}
          loading={saving}
          disabled={!writable || byteLength > (document?.max_bytes ?? 65536)}
        >
          {t("common.save", "保存")}
        </Button>
      </Space>
      <Button onClick={() => void runReview()} loading={reviewing}>
        {t("memory.review.run", "审查手动文档与结构化记忆")}
      </Button>
      {review?.status === "unavailable" && (
        <Alert
          type="error"
          showIcon
          message={t(
            "memory.review.unavailable",
            "语义审查暂不可用，未作任何更改",
          )}
          description={review.message || review.reason}
        />
      )}
      {review?.status === "reviewed" && (
        <>
          <Alert
            type="info"
            showIcon
            message={t(
              "memory.review.authoritative",
              "手动文档保持权威；审查结果仅供确认",
            )}
            description={t(
              "memory.review.coverage",
              "已审查 {{reviewed}} / {{total}} 条结构化事实",
              {
                reviewed: review.coverage.facts_reviewed,
                total: review.coverage.facts_total,
              },
            )}
          />
          {review.coverage.complete === false && (
            <Alert
              type="warning"
              message={t(
                "memory.review.partial",
                "事实数量达到本次审查上限，覆盖不完整",
              )}
            />
          )}
          {(review.conflicts ?? []).map((item: MemoryReviewConflict) => (
            <Alert
              key={`${item.id}:${item.document}`}
              type={item.relation === "contradiction" ? "warning" : "info"}
              showIcon
              message={
                <Space>
                  <Tag>{item.relation}</Tag>
                  <Typography.Text>{item.document}</Typography.Text>
                </Space>
              }
              description={
                <Space direction="vertical">
                  <Typography.Text>
                    {t("memory.review.fact", "结构化事实")}: {item.fact}
                  </Typography.Text>
                  <Typography.Text>
                    {t("memory.review.evidence", "文档证据")}: {item.evidence}
                  </Typography.Text>
                  {item.relation === "contradiction" && (
                    <Checkbox
                      checked={selectedFacts.includes(item.id)}
                      onChange={(event) =>
                        setSelectedFacts((current) =>
                          event.target.checked
                            ? [...new Set([...current, item.id])]
                            : current.filter((id) => id !== item.id),
                        )
                      }
                    >
                      {t(
                        "memory.review.keepManual",
                        "保留手动版本并停用此矛盾事实",
                      )}
                    </Checkbox>
                  )}
                </Space>
              }
            />
          ))}
          {(review.conflicts?.length ?? 0) === 0 && (
            <Typography.Text type="secondary">
              {t(
                "memory.review.none",
                "未发现有证据支持的冲突或重复；这不代表绝对无冲突。",
              )}
            </Typography.Text>
          )}
          <Button
            danger
            onClick={() => void applyReview()}
            loading={applying}
            disabled={
              !writable ||
              !review.receipt ||
              selectedFacts.length === 0 ||
              review.coverage.complete === false
            }
          >
            {t("memory.review.apply", "确认所选项：保留手动版本")}
          </Button>
        </>
      )}
    </Space>
  );
}
