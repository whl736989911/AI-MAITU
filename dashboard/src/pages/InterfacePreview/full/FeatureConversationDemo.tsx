import { useState } from "react";
import { Button, Card, Input, List, Space, Tag, Typography } from "antd";
import { useDemoState } from "./DemoKit";
import styles from "./WorkspacePages.module.less";

interface FeatureConversationMessage {
  id: string;
  role: "user" | "sample";
  text: string;
}

interface FeatureConversationDemoProps {
  featureId: string;
  featureName: string;
  t: (key: string) => string;
}

export default function FeatureConversationDemo({
  featureId,
  featureName,
  t,
}: FeatureConversationDemoProps) {
  const [messages, setMessages] = useDemoState<FeatureConversationMessage[]>(
    `workspace.featureConversation.${featureId}`,
    [
      {
        id: `${featureId}-sample-question`,
        role: "user",
        text: t("interfacePreview.full.workspace.featureSampleQuestion"),
      },
      {
        id: `${featureId}-sample-answer`,
        role: "sample",
        text: t("interfacePreview.full.workspace.featureSampleAnswer"),
      },
    ],
  );
  const [draft, setDraft] = useState("");
  const addQuestion = () => {
    const text = draft.trim();
    if (!text) return;
    setMessages((current) => [
      ...current,
      { id: `question-${Date.now()}`, role: "user", text },
    ]);
    setDraft("");
  };

  return (
    <Card
      className={styles.featureConversation}
      title={`${featureName} · ${t(
        "interfacePreview.full.workspace.featureConversationTitle",
      )}`}
      extra={
        <Tag color="blue">
          {t("interfacePreview.full.workspace.featureConversationLocalOnly")}
        </Tag>
      }
    >
      <List
        dataSource={messages}
        renderItem={(message) => (
          <List.Item>
            <div
              className={
                message.role === "user"
                  ? styles.featureQuestion
                  : styles.featureSampleAnswer
              }
            >
              <Space>
                <Typography.Text strong>
                  {message.role === "user"
                    ? t("interfacePreview.full.workspace.senderSelf")
                    : t(
                        "interfacePreview.full.workspace.featureSampleAnswerLabel",
                      )}
                </Typography.Text>
                {message.role === "sample" && (
                  <Tag>
                    {t("interfacePreview.full.workspace.featureSampleOnly")}
                  </Tag>
                )}
              </Space>
              <Typography.Paragraph
                style={{ margin: "8px 0 0", whiteSpace: "pre-wrap" }}
              >
                {message.text}
              </Typography.Paragraph>
            </div>
          </List.Item>
        )}
      />
      <div className={styles.featureComposer}>
        <Input.TextArea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          autoSize={{ minRows: 2, maxRows: 4 }}
          placeholder={t(
            "interfacePreview.full.workspace.featureConversationPlaceholder",
          )}
        />
        <div className={styles.featureComposerActions}>
          <Typography.Text type="secondary">
            {t("interfacePreview.full.workspace.featureConversationBoundary")}
          </Typography.Text>
          <Button type="primary" disabled={!draft.trim()} onClick={addQuestion}>
            {t("interfacePreview.full.workspace.featureAddLocalQuestion")}
          </Button>
        </div>
      </div>
    </Card>
  );
}
