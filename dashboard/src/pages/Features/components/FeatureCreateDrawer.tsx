/**
 * New feature — what a feature declares about itself, and the only form a feature
 * has of its own.
 *
 * Creating a feature creates the agent that carries it, so this drawer asks for
 * the agent's own beginnings and nothing else: the id it will be named after, what
 * it is called, what it is for, and the model it runs with. Everything an expert is
 * configured with — skills, tools, plugins, subagents, MBTI, memory, channels, the
 * persona files, the backend, the path mappings — is the feature's own page after
 * this, over the same panels an expert is configured with, so none of it is asked
 * twice or answered in two places.
 *
 * The model select, its "auto" sentinel and the no-models warning are the expert
 * create flow's own (``utils/modelOptions``, ``useAgentFormResources``), because a
 * feature's agent is an agent and its model is chosen the same way.
 */

import { useCallback, useEffect, useState } from "react";
import { Alert, Drawer, Form, Input, Select } from "antd";
import { useTranslation } from "react-i18next";
import { message } from "@/utils/antdMessage";

import {
  featuresApi,
  type FeatureTemplate,
} from "../../../api/modules/features";
import { apiErrorMessage } from "../../../utils/apiError";
import {
  MODEL_AUTO_VALUE,
  buildModelSelectOptions,
  defaultModelFromForm,
} from "../../../utils/modelOptions";
import { useAgentFormResources } from "../../../hooks/useAgentFormResources";
import styles from "../../Experts/index.module.less";

interface FeatureFormValues {
  feature_id: string;
  name: string;
  description?: string;
  default_model: string;
  conversation_mode: "ask" | "plan" | "craft";
  template_name?: string;
}

export interface FeatureCreateDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Called with the new feature's agent id once it exists. */
  onCreated: (agentId: string) => void;
}

export default function FeatureCreateDrawer({
  open,
  onClose,
  onCreated,
}: FeatureCreateDrawerProps) {
  const { t, i18n } = useTranslation();
  const [form] = Form.useForm<FeatureFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [templates, setTemplates] = useState<FeatureTemplate[]>([]);

  const { models, modelsLoading } = useAgentFormResources(open);
  const hasNoModels = !modelsLoading && models.length === 0;
  const modelOptions = buildModelSelectOptions(
    models,
    t("experts.defaultModelAuto"),
  );
  useEffect(() => {
    if (!open) return;
    void featuresApi
      .templates()
      .then(setTemplates)
      .catch(() => setTemplates([]));
  }, [open]);

  const handleCreate = useCallback(async () => {
    let values: FeatureFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return; // the field's own message is on screen
    }
    setSubmitting(true);
    try {
      const created = await featuresApi.create({
        feature_id: values.feature_id.trim(),
        name: values.name.trim(),
        description: values.description?.trim() || null,
        default_model: defaultModelFromForm(values.default_model),
        conversation_mode: values.conversation_mode,
        template_name: values.template_name || null,
      });
      message.success(t("features.created", { name: created.name }));
      form.resetFields();
      onCreated(created.agent_id);
    } catch (err: unknown) {
      message.error(apiErrorMessage(err, t("features.createFailed"), t));
    } finally {
      setSubmitting(false);
    }
  }, [form, onCreated, t]);

  return (
    <Drawer
      open={open}
      title={t("features.create")}
      width={520}
      onClose={onClose}
      destroyOnHidden
      footer={
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className={styles.drawerCancelBtn} onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            className={styles.drawerSaveBtn}
            onClick={() => void handleCreate()}
            disabled={submitting || hasNoModels}
            title={hasNoModels ? t("experts.noModelsWarning") : undefined}
          >
            {submitting ? t("experts.creating") : t("common.create")}
          </button>
        </div>
      }
    >
      {hasNoModels && (
        <Alert
          type="warning"
          showIcon
          message={t("experts.noModelsWarning")}
          action={
            <a href="/admin/models" style={{ whiteSpace: "nowrap" }}>
              {t("experts.goToAdmin")}
            </a>
          }
          style={{ marginBottom: 16 }}
        />
      )}

      <Form
        form={form}
        layout="vertical"
        size="middle"
        initialValues={{
          default_model: MODEL_AUTO_VALUE,
          conversation_mode: "craft",
        }}
      >
        <Form.Item
          name="feature_id"
          label={t("features.id")}
          extra={t("features.idHint")}
          rules={[{ required: true, message: t("features.idRequired") }]}
        >
          <Input placeholder={t("features.idPlaceholder")} autoComplete="off" />
        </Form.Item>

        <Form.Item
          name="name"
          label={t("features.name")}
          rules={[{ required: true, message: t("features.nameRequired") }]}
        >
          <Input autoComplete="off" />
        </Form.Item>

        <Form.Item name="description" label={t("features.description")}>
          <Input.TextArea rows={3} />
        </Form.Item>

        <Form.Item
          name="template_name"
          label={t("features.templateLabel")}
          extra={t("features.templateHint")}
        >
          <Select
            allowClear
            placeholder={t("features.templateNone")}
            options={templates.map((template) => ({
              value: template.id,
              label:
                (i18n.resolvedLanguage?.startsWith("zh")
                  ? template.label.zh
                  : template.label.en) || template.id,
            }))}
          />
        </Form.Item>
        <Form.Item name="default_model" label={t("experts.defaultModelLabel")}>
          <Select
            loading={modelsLoading}
            options={modelOptions}
            placeholder={t("experts.defaultModelPlaceholder")}
            showSearch
            filterOption={(input, opt) =>
              ((opt?.label as string) ?? "")
                .toLowerCase()
                .includes(input.toLowerCase())
            }
          />
        </Form.Item>
        <Form.Item
          name="conversation_mode"
          label={t("experts.conversationModeLabel")}
          extra={t("experts.conversationModeHint")}
        >
          <Select
            options={[
              { value: "ask", label: t("chat.conversationMode.ask") },
              { value: "plan", label: t("chat.conversationMode.plan") },
              { value: "craft", label: t("chat.conversationMode.craft") },
            ]}
          />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
