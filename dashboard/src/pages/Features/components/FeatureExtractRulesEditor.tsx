/**
 * Form editor for field-extraction rules.
 *
 * Managers should not have to hand-write JSON: this exposes profiles (which
 * drawing layout applies) and their field mappings (which title-block label
 * fills which ledger field) as plain inputs. The server validates the resulting
 * document, so a bad field key or regex is reported instead of stored.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  Select,
  Space,
  Spin,
  Tag,
} from "antd";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { message } from "@/utils/antdMessage";

import {
  featureDataApi,
  type FeatureDataSchema,
} from "../../../api/modules/featureData";
import { apiErrorMessage } from "../../../utils/apiError";
import {
  draftProblem,
  draftsFromRules,
  newDraftId,
  rulesFromDrafts,
  schemaFieldKeys,
  type ExtractFieldDraft,
  type ExtractProfileDraft,
} from "../utils/extractRules";

export interface FeatureExtractRulesEditorProps {
  agentId: string;
  schema: FeatureDataSchema | null;
}

function emptyField(fieldKey: string): ExtractFieldDraft {
  return {
    id: newDraftId("field"),
    fieldKey,
    source: "label",
    labels: "",
    pattern: "",
    unitKg: false,
    pick: "max_outer",
    limit: "",
  };
}

function emptyProfile(fieldKey: string): ExtractProfileDraft {
  return {
    id: newDraftId("profile"),
    name: "",
    labelsAny: "",
    filenamePattern: "",
    fields: [emptyField(fieldKey)],
  };
}

export default function FeatureExtractRulesEditor({
  agentId,
  schema,
}: FeatureExtractRulesEditorProps) {
  const { t, i18n } = useTranslation();
  const [drafts, setDrafts] = useState<ExtractProfileDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fieldKeys = useMemo(() => schemaFieldKeys(schema), [schema]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const answer = await featureDataApi.getExtractRules(agentId);
      setDrafts(
        answer.rules
          ? draftsFromRules(answer.rules, i18n.language)
          : [] as ExtractProfileDraft[],
      );
      if (answer.error) setError(answer.error);
    } catch (exc) {
      setError(
        apiErrorMessage(
          exc,
          t("features.data.extractRules.loadFailed", "读取抽取规则失败"),
          t,
        ),
      );
    } finally {
      setLoading(false);
    }
  }, [agentId, i18n.language, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const patchProfile = (id: string, patch: Partial<ExtractProfileDraft>) => {
    setDrafts((rows) => rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  };

  const patchField = (
    profileId: string,
    fieldId: string,
    patch: Partial<ExtractFieldDraft>,
  ) => {
    setDrafts((rows) =>
      rows.map((row) =>
        row.id === profileId
          ? {
              ...row,
              fields: row.fields.map((field) =>
                field.id === fieldId ? { ...field, ...patch } : field,
              ),
            }
          : row,
      ),
    );
  };

  const save = async () => {
    const problem = draftProblem(drafts, schema);
    if (problem) {
      message.warning(
        problem === "empty"
          ? t("features.data.extractRules.empty", "至少添加一个版式")
          : t(
              "features.data.extractRules.noFields",
              "每个版式至少需要一个字段映射",
            ),
      );
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const answer = await featureDataApi.putExtractRules(
        agentId,
        rulesFromDrafts(drafts, i18n.language),
      );
      setDrafts(draftsFromRules(answer.rules, i18n.language));
      message.success(
        t("features.data.extractRules.saved", "抽取规则已保存"),
      );
    } catch (exc) {
      setError(
        apiErrorMessage(
          exc,
          t("features.data.extractRules.saveFailed", "保存抽取规则失败"),
          t,
        ),
      );
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <Spin />
      </div>
    );
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message={t(
          "features.data.extractRules.hint",
          "每个版式按图纸文字或文件名匹配；命中的版式决定字段从标题栏哪里取值。",
        )}
      />
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {drafts.length === 0 ? (
        <Empty
          description={t(
            "features.data.extractRules.empty",
            "尚未配置抽取规则",
          )}
        />
      ) : null}
      {drafts.map((profile) => (
        <Card
          key={profile.id}
          size="small"
          title={
            <Input
              value={profile.name}
              placeholder={t(
                "features.data.extractRules.profileName",
                "版式名称（如：锻造工艺卡）",
              )}
              onChange={(event) =>
                patchProfile(profile.id, { name: event.target.value })
              }
            />
          }
          extra={
            <Button
              danger
              type="text"
              size="small"
              icon={<Trash2 size={14} />}
              onClick={() =>
                setDrafts((rows) => rows.filter((row) => row.id !== profile.id))
              }
            />
          }
        >
          <Space direction="vertical" size="small" style={{ width: "100%" }}>
            <Space wrap>
              <Input
                style={{ width: 260 }}
                addonBefore={t(
                  "features.data.extractRules.whenLabels",
                  "包含文字",
                )}
                value={profile.labelsAny}
                placeholder={t(
                  "features.data.extractRules.whenLabelsPlaceholder",
                  "任意一个出现即匹配，逗号分隔",
                )}
                onChange={(event) =>
                  patchProfile(profile.id, { labelsAny: event.target.value })
                }
              />
              <Input
                style={{ width: 240 }}
                addonBefore={t(
                  "features.data.extractRules.whenFilename",
                  "文件名匹配",
                )}
                value={profile.filenamePattern}
                placeholder={t(
                  "features.data.extractRules.whenFilenamePlaceholder",
                  "正则，如 模具",
                )}
                onChange={(event) =>
                  patchProfile(profile.id, {
                    filenamePattern: event.target.value,
                  })
                }
              />
            </Space>
            {profile.fields.map((field) => (
              <Space key={field.id} wrap align="start">
                <Select
                  style={{ width: 180 }}
                  value={field.fieldKey || undefined}
                  placeholder={t("features.data.extractRules.field", "字段")}
                  options={fieldKeys.map((key) => ({ value: key, label: key }))}
                  onChange={(value) =>
                    patchField(profile.id, field.id, { fieldKey: value })
                  }
                />
                <Select
                  style={{ width: 150 }}
                  value={field.source}
                  options={[
                    {
                      value: "label",
                      label: t(
                        "features.data.extractRules.sourceLabel",
                        "标题栏文字",
                      ),
                    },
                    {
                      value: "dimensions",
                      label: t(
                        "features.data.extractRules.sourceDimensions",
                        "尺寸标注",
                      ),
                    },
                  ]}
                  onChange={(value) =>
                    patchField(profile.id, field.id, { source: value })
                  }
                />
                {field.source === "label" ? (
                  <>
                    <Input
                      style={{ width: 240 }}
                      value={field.labels}
                      placeholder={t(
                        "features.data.extractRules.labels",
                        "标签，逗号分隔",
                      )}
                      onChange={(event) =>
                        patchField(profile.id, field.id, {
                          labels: event.target.value,
                        })
                      }
                    />
                    <Input
                      style={{ width: 200 }}
                      value={field.pattern}
                      placeholder={t(
                        "features.data.extractRules.pattern",
                        "正则兜底（可选）",
                      )}
                      onChange={(event) =>
                        patchField(profile.id, field.id, {
                          pattern: event.target.value,
                        })
                      }
                    />
                    <Checkbox
                      checked={field.unitKg}
                      onChange={(event) =>
                        patchField(profile.id, field.id, {
                          unitKg: event.target.checked,
                        })
                      }
                    >
                      kg
                    </Checkbox>
                  </>
                ) : (
                  <>
                    <Select
                      style={{ width: 160 }}
                      value={field.pick}
                      options={[
                        {
                          value: "max_outer",
                          label: t(
                            "features.data.extractRules.pickMaxOuter",
                            "最大外径",
                          ),
                        },
                        {
                          value: "min_inner",
                          label: t(
                            "features.data.extractRules.pickMinInner",
                            "最小内径",
                          ),
                        },
                        {
                          value: "text",
                          label: t(
                            "features.data.extractRules.pickText",
                            "原文前 N 条",
                          ),
                        },
                      ]}
                      onChange={(value) =>
                        patchField(profile.id, field.id, { pick: value })
                      }
                    />
                    {field.pick === "text" ? (
                      <Input
                        style={{ width: 100 }}
                        value={field.limit}
                        placeholder={t(
                          "features.data.extractRules.limit",
                          "条数",
                        )}
                        onChange={(event) =>
                          patchField(profile.id, field.id, {
                            limit: event.target.value,
                          })
                        }
                      />
                    ) : null}
                  </>
                )}
                <Button
                  type="text"
                  danger
                  size="small"
                  icon={<Trash2 size={14} />}
                  onClick={() =>
                    patchProfile(profile.id, {
                      fields: profile.fields.filter(
                        (row) => row.id !== field.id,
                      ),
                    })
                  }
                />
              </Space>
            ))}
            <Button
              size="small"
              icon={<Plus size={14} />}
              onClick={() =>
                patchProfile(profile.id, {
                  fields: [
                    ...profile.fields,
                    emptyField(fieldKeys[0] ?? ""),
                  ],
                })
              }
            >
              {t("features.data.extractRules.addField", "添加字段")}
            </Button>
          </Space>
        </Card>
      ))}
      <Space>
        <Button
          icon={<Plus size={14} />}
          onClick={() =>
            setDrafts((rows) => [...rows, emptyProfile(fieldKeys[0] ?? "")])
          }
        >
          {t("features.data.extractRules.addProfile", "添加版式")}
        </Button>
        <Button type="primary" loading={saving} onClick={() => void save()}>
          {t("common.save", "保存")}
        </Button>
        {fieldKeys.length === 0 ? (
          <Tag color="orange">
            {t(
              "features.data.extractRules.noSchema",
              "请先在“数据结构”里定义字段",
            )}
          </Tag>
        ) : null}
      </Space>
    </Space>
  );
}
