import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  InputNumber,
  Input,
  Radio,
  Select,
  Skeleton,
  Space,
  Switch,
  Typography,
} from "antd";
import { message } from "@/utils/antdMessage";

import { Brain, Cpu, Database, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  memoryDashboardApi,
  type ExtractConfig,
  type ExtractTriggerMode,
} from "../../../api/modules/memoryDashboard";
import { providerApi } from "../../../api/modules/provider";
import {
  MODEL_AUTO_VALUE,
  buildModelSelectOptions,
  defaultModelFromForm,
  defaultModelToForm,
  type ModelPickerOption,
} from "../../../utils/modelOptions";
import styles from "./MemorySettings.module.less";

interface Props {
  agentId: string;
}

const MIN_IDLE_MINUTES = 1;
const MIN_INTERVAL_HOURS = 0.1;

export default function MemorySettings({ agentId }: Props) {
  const { t } = useTranslation();
  const loadFailedMessage = t("memory.settings.loadFailed", "记忆设置加载失败");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [mode, setMode] = useState<ExtractTriggerMode>("idle");
  const [idleMinutes, setIdleMinutes] = useState<number | null>(5);
  const [intervalHours, setIntervalHours] = useState<number | null>(6);
  const [auxModel, setAuxModel] = useState<string>(MODEL_AUTO_VALUE);
  const [lightTimeout, setLightTimeout] = useState(0);
  const [heavyTimeout, setHeavyTimeout] = useState(0);
  const [maxTokens, setMaxTokens] = useState(0);
  const [extraBody, setExtraBody] = useState("{}");
  const [modelsLoading, setModelsLoading] = useState(false);
  const [models, setModels] = useState<ModelPickerOption[]>([]);

  useEffect(() => {
    let cancelled = false;
    setModelsLoading(true);
    providerApi
      .listResolvedModels()
      .then((data) => {
        if (!cancelled) setModels(data as ModelPickerOption[]);
      })
      .catch(() => {
        if (!cancelled) setModels([]);
      })
      .finally(() => {
        if (!cancelled) setModelsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const applyConfig = useCallback((cfg: ExtractConfig) => {
    setMemoryEnabled(cfg.memory_enabled ?? true);
    setMode(cfg.extract_trigger_mode);
    setAuxModel(defaultModelToForm(cfg.aux_model));
    setIdleMinutes(
      Math.max(
        MIN_IDLE_MINUTES,
        Math.round((cfg.extract_idle_seconds / 60) * 10) / 10,
      ),
    );
    setIntervalHours(
      Math.round((cfg.extract_interval_seconds / 3600) * 10) / 10,
    );
    setLightTimeout(cfg.extract_light_timeout_s ?? 0);
    setHeavyTimeout(cfg.extract_heavy_timeout_s ?? 0);
    setMaxTokens(cfg.extract_max_tokens ?? 0);
    setExtraBody(JSON.stringify(cfg.extract_extra_body ?? {}, null, 2));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    memoryDashboardApi
      .getExtractConfig(agentId)
      .then((cfg) => {
        if (!cancelled) applyConfig(cfg);
      })
      .catch(() => {
        if (!cancelled) {
          message.error(loadFailedMessage);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId, applyConfig, loadFailedMessage]);

  const handleSave = async () => {
    let parsedExtraBody: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(extraBody);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("extra_body must be a JSON object");
      }
      parsedExtraBody = parsed as Record<string, unknown>;
    } catch {
      message.error(
        t(
          "memory.settings.extraBodyInvalid",
          "额外请求参数必须是有效的 JSON 对象",
        ),
      );
      return;
    }
    setSaving(true);
    try {
      const cfg = await memoryDashboardApi.putExtractConfig(agentId, {
        memory_enabled: memoryEnabled,
        extract_on_session_end: true,
        extract_trigger_mode: mode,
        extract_idle_seconds: Math.round(
          Math.max(MIN_IDLE_MINUTES, idleMinutes ?? MIN_IDLE_MINUTES) * 60,
        ),
        extract_interval_seconds: Math.round(
          Math.max(MIN_INTERVAL_HOURS, intervalHours ?? MIN_INTERVAL_HOURS) *
            3600,
        ),
        aux_model: defaultModelFromForm(auxModel) ?? "",
        extract_light_timeout_s: lightTimeout,
        extract_heavy_timeout_s: heavyTimeout,
        extract_max_tokens: maxTokens,
        extract_extra_body: parsedExtraBody,
      });
      applyConfig(cfg);
      message.success(t("memory.settings.saved", "已保存，agent 将自动重载"));
    } catch {
      message.error(t("memory.settings.saveFailed", "保存失败"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className={styles.settingsPage}>
        <Skeleton active paragraph={{ rows: 8 }} />
      </div>
    );
  }

  return (
    <div className={styles.settingsPage}>
      <header className={styles.pageHeader}>
        <div className={styles.headerIcon}>
          <Brain size={22} />
        </div>
        <div>
          <h2>{t("memory.settings.title", "记忆设置")}</h2>
          <p>
            {t(
              "memory.settings.description",
              "控制这个 Agent 是否使用记忆，以及对话内容何时被整理成长期记忆。",
            )}
          </p>
        </div>
      </header>

      <Card className={styles.settingCard}>
        <div className={styles.settingRow}>
          <div className={styles.settingIdentity}>
            <span className={styles.settingIcon}>
              <Database size={18} />
            </span>
            <div>
              <div className={styles.settingTitle}>
                {t("memory.settings.storageTitle", "存储记忆")}
              </div>
              <div className={styles.settingDescription}>
                {t(
                  "memory.settings.storageDescription",
                  "允许 Agent 读取已有记忆，并从新对话中持续积累记忆。",
                )}
              </div>
            </div>
          </div>
          <div className={styles.switchBlock}>
            <span
              className={memoryEnabled ? styles.statusOn : styles.statusOff}
            >
              {memoryEnabled
                ? t("memory.settings.enabled", "已开启")
                : t("memory.settings.disabled", "已关闭")}
            </span>
            <Switch checked={memoryEnabled} onChange={setMemoryEnabled} />
          </div>
        </div>

        {!memoryEnabled ? (
          <Alert
            className={styles.memoryWarning}
            type="warning"
            showIcon
            message={t(
              "memory.settings.disabledTitle",
              "关闭后 Agent 将不再使用记忆",
            )}
            description={t(
              "memory.settings.disabledDescription",
              "Agent 不会读取已有记忆，也不会捕获或提炼新的记忆。已有记忆和对话记录不会被删除，重新开启后可以继续使用。",
            )}
          />
        ) : null}
      </Card>

      <Card
        className={`${styles.settingCard} ${
          !memoryEnabled ? styles.cardDisabled : ""
        }`}
      >
        <div className={styles.sectionHeading}>
          <span className={styles.settingIcon}>
            <Sparkles size={18} />
          </span>
          <div>
            <div className={styles.settingTitle}>
              {t("memory.settings.distillTitle", "记忆提炼时机")}
            </div>
            <div className={styles.settingDescription}>
              {t(
                "memory.settings.distillDescription",
                "选择何时把对话记忆整理为可召回的长期记忆。",
              )}
            </div>
          </div>
        </div>

        <fieldset className={styles.strategyFields} disabled={!memoryEnabled}>
          <Radio.Group
            value={mode}
            onChange={(event) =>
              setMode(event.target.value as ExtractTriggerMode)
            }
          >
            <Space direction="vertical" size={14}>
              <Radio value="idle">
                <span className={styles.radioTitle}>
                  {t("memory.extractConfig.modeIdle", "对话空闲后提炼")}
                </span>
                <Typography.Text type="secondary" className={styles.radioHint}>
                  {t(
                    "memory.extractConfig.modeIdleHint",
                    "等对话安静一段时间再提炼，记忆质量最好（推荐）",
                  )}
                </Typography.Text>
              </Radio>
              <Radio value="interval">
                <span className={styles.radioTitle}>
                  {t("memory.extractConfig.modeInterval", "固定间隔提炼")}
                </span>
                <Typography.Text type="secondary" className={styles.radioHint}>
                  {t(
                    "memory.extractConfig.modeIntervalHint",
                    "按固定周期批量整理，可能包含尚未结束的对话",
                  )}
                </Typography.Text>
              </Radio>
            </Space>
          </Radio.Group>

          <div className={styles.timeControl}>
            {mode === "idle" ? (
              <>
                <span>{t("memory.extractConfig.idlePrefix", "对话空闲")}</span>
                <InputNumber
                  aria-label={t("memory.extractConfig.idlePrefix", "对话空闲")}
                  min={MIN_IDLE_MINUTES}
                  max={7 * 24 * 60}
                  value={idleMinutes}
                  onChange={setIdleMinutes}
                />
                <span>{t("memory.extractConfig.minutes", "分钟后提炼")}</span>
              </>
            ) : (
              <>
                <span>{t("memory.extractConfig.intervalPrefix", "每隔")}</span>
                <InputNumber
                  aria-label={t("memory.extractConfig.intervalPrefix", "每隔")}
                  min={MIN_INTERVAL_HOURS}
                  max={7 * 24}
                  step={0.5}
                  value={intervalHours}
                  onChange={setIntervalHours}
                />
                <span>{t("memory.extractConfig.hours", "小时提炼一次")}</span>
              </>
            )}
          </div>

          {mode === "interval" ? (
            <Alert
              type="info"
              showIcon
              message={t(
                "memory.extractConfig.intervalNote",
                "固定间隔可能在会话尚未结束时运行；多数场景推荐使用“对话空闲后提炼”。",
              )}
            />
          ) : null}
        </fieldset>
      </Card>

      <Card
        className={`${styles.settingCard} ${
          !memoryEnabled ? styles.cardDisabled : ""
        }`}
      >
        <div className={styles.sectionHeading}>
          <span className={styles.settingIcon}>
            <Cpu size={18} />
          </span>
          <div>
            <div className={styles.settingTitle}>
              {t("memory.settings.extractModelTitle", "记忆提取模型")}
            </div>
            <div className={styles.settingDescription}>
              {t(
                "memory.settings.extractModelDescription",
                "提炼记忆时调用的模型。选择“自动”跟随对话使用的默认模型；也可以指定一个更便宜或更快的模型专门做提炼。",
              )}
            </div>
          </div>
        </div>
        <fieldset className={styles.strategyFields} disabled={!memoryEnabled}>
          <Select
            style={{ minWidth: 280, maxWidth: 420 }}
            value={auxModel}
            loading={modelsLoading}
            disabled={!memoryEnabled}
            onChange={setAuxModel}
            options={buildModelSelectOptions(
              models,
              t("memory.settings.extractModelAuto", "自动（跟随对话模型）"),
            )}
            showSearch
            optionFilterProp="label"
          />
        </fieldset>
      </Card>
      <Card className={styles.settingCard}>
        <div className={styles.settingTitle}>
          {t("memory.settings.budgetTitle", "提取请求预算")}
        </div>
        <div className={styles.settingDescription}>
          {t(
            "memory.settings.budgetDescription",
            "按 light/heavy 调用分别设置超时；0 表示使用运行时默认值。",
          )}
        </div>
        <fieldset className={styles.strategyFields} disabled={!memoryEnabled}>
          <Space wrap>
            <label>
              {t("memory.settings.lightTimeout", "Light 超时（秒）")}
              <InputNumber
                min={0}
                max={3600}
                value={lightTimeout}
                onChange={(value) => setLightTimeout(value ?? 0)}
              />
            </label>
            <label>
              {t("memory.settings.heavyTimeout", "Heavy 超时（秒）")}
              <InputNumber
                min={0}
                max={3600}
                value={heavyTimeout}
                onChange={(value) => setHeavyTimeout(value ?? 0)}
              />
            </label>
            <label>
              {t("memory.settings.maxTokens", "最大输出 token")}
              <InputNumber
                min={0}
                max={65536}
                value={maxTokens}
                onChange={(value) => setMaxTokens(value ?? 0)}
              />
            </label>
          </Space>
          <label style={{ display: "block", marginTop: 16 }}>
            {t("memory.settings.extraBody", "额外模型请求参数（JSON object）")}
            <Input.TextArea
              value={extraBody}
              onChange={(event) => setExtraBody(event.target.value)}
              autoSize={{ minRows: 3, maxRows: 8 }}
            />
          </label>
        </fieldset>
      </Card>

      <div className={styles.saveBar}>
        <span>
          {t(
            "memory.settings.reloadHint",
            "保存后 Agent 会自动重载，当前对话不会被删除。",
          )}
        </span>
        <Button type="primary" loading={saving} onClick={handleSave}>
          {t("common.save", "保存设置")}
        </Button>
      </div>
    </div>
  );
}
