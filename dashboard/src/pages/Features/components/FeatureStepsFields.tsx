/**
 * The task-step block of a feature definition (design 7.10): the ordered
 * skeleton a run walks, each step consuming the typed artifacts of the ones
 * before it.
 *
 * What the author sees here is what a run will do — nothing about a step is
 * implied. Two fields of the schema say how the step runs: ``mode`` picks
 * between one agent doing the step's job and an agent that decomposes the step
 * and dispatches subagents itself, and ``agent_role`` names the subagent a step
 * runs as.
 *
 * The tool whitelist needs the caller's tool catalogue, which costs an agent
 * start; like the capability block it is fetched only once this block is opened,
 * carries its own refusal and its own retry, and never falls back to an empty
 * list — "no tools" is a scope the author can declare, so it must not be the
 * accidental result of a failed call.
 */

import { InputNumber, Alert, AutoComplete, Button, Checkbox, Collapse, Form, Input, Select, Spin, Tooltip } from "antd";
import { ArrowDown, ArrowUp, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  FeatureStep,
  FeatureStepMode,
} from "../../../api/modules/features";
import {
  artifactNamesBefore,
  emptyStep,
  FEATURE_STEP_FAILURES,
  FEATURE_STEP_GATES,
  FEATURE_STEP_MODES,
  isValidStepId,
} from "./featureSettings";
import { DeclaredListField } from "./FeatureCapabilityFields";
import { useFeatureCapabilities } from "./useFeatureCapabilities";
import styles from "../index.module.less";

const GATE_LABEL_KEYS: Record<string, string> = {
  auto: "features.settingsStepGateAuto",
  confirm: "features.settingsStepGateConfirm",
  validate: "features.settingsStepGateValidate",
};

const FAILURE_LABEL_KEYS: Record<string, string> = {
  abort: "features.settingsStepFailureAbort",
  escalate: "features.settingsStepFailureEscalate",
  retry: "features.settingsStepFailureRetry",
};

const MODE_LABEL_KEYS: Record<FeatureStepMode, string> = {
  agent: "features.settingsStepModeAgent",
  orchestrate: "features.settingsStepModeOrchestrate",
};

/** The artifact schemas the definition format accepts, offered as suggestions. */
const OUTPUT_SCHEMA_OPTIONS = [
  "text",
  "list",
  "object",
  "table",
  "table:12cols",
];

/** One place for "must be text": whitespace-only input is not text. */
function requiredRule(message: string) {
  return { required: true, whitespace: true, message };
}

export interface FeatureStepsFieldsProps {
  /** The step block is only rendered once its disclosure is opened. */
  open: boolean;
}

export default function FeatureStepsFields({ open }: FeatureStepsFieldsProps) {
  const { t } = useTranslation();
  const { choices, loading, error, load } = useFeatureCapabilities(open);
  const form = Form.useFormInstance<Record<string, unknown>>();
  // Watched, not read once: a card's controls depend on its own gate and output
  // schema, and those change as the author types.
  const steps = Form.useWatch<FeatureStep[]>("steps", form) ?? [];

  if (loading) {
    return (
      <div className={styles.capabilityStatus}>
        <Spin size="small" />
        <span>{t("features.settingsCapabilityLoading")}</span>
      </div>
    );
  }

  if (error !== null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("features.settingsCapabilityLoadFailed")}
        description={error}
        action={
          <Button
            size="small"
            icon={<RefreshCw size={13} />}
            onClick={() => void load()}
          >
            {t("features.settingsCapabilityRetry")}
          </Button>
        }
      />
    );
  }

  if (choices === null) {
    return null;
  }

  return (
    <Form.List name="steps">
      {(rows, { add, remove, move }) => (
        <div className={styles.fieldList}>
          {rows.length === 0 && (
            <div className={styles.fieldEmpty}>
              {t("features.settingsStepsEmpty")}
            </div>
          )}

          {rows.map((field, index) => {
            const step = steps[field.name];
            const gated = step?.gate === "confirm" || step?.gate === "validate";
            return (
              <article className={styles.fieldCard} key={field.key}>
                <div className={styles.fieldCardHead}>
                  <Form.Item
                    name={[field.name, "id"]}
                    className={styles.fieldNameItem}
                    rules={[
                      requiredRule(t("features.settingsStepIdRequired")),
                      ({ getFieldValue }) => ({
                        validator: async (_, value: string) => {
                          const id = (value ?? "").trim();
                          if (!id) return;
                          if (!isValidStepId(id)) {
                            throw new Error(t("features.settingsStepIdInvalid"));
                          }
                          const used: FeatureStep[] =
                            getFieldValue("steps") ?? [];
                          // A rewind names a step by its id, and a step's
                          // artifacts are keyed by it: two steps sharing one id
                          // make both unaddressable.
                          const clash = used.filter(
                            (item) => item.id?.trim() === id,
                          );
                          if (clash.length > 1) {
                            throw new Error(
                              t("features.settingsStepIdDuplicate"),
                            );
                          }
                        },
                      }),
                    ]}
                  >
                    <Input
                      placeholder="op_design"
                      autoComplete="off"
                    />
                  </Form.Item>

                  <Form.Item
                    name={[field.name, "name"]}
                    className={styles.fieldNameItem}
                    rules={[
                      requiredRule(t("features.settingsStepNameRequired")),
                    ]}
                  >
                    <Input
                      placeholder={t("features.settingsStepNamePlaceholder")}
                      autoComplete="off"
                    />
                  </Form.Item>

                  <div className={styles.fieldCardTools}>
                    <Tooltip title={t("features.settingsFieldMoveUp")}>
                      <Button
                        type="text"
                        size="small"
                        aria-label={t("features.settingsFieldMoveUp")}
                        disabled={index === 0}
                        icon={<ArrowUp size={13} />}
                        onClick={() => move(index, index - 1)}
                      />
                    </Tooltip>
                    <Tooltip title={t("features.settingsFieldMoveDown")}>
                      <Button
                        type="text"
                        size="small"
                        aria-label={t("features.settingsFieldMoveDown")}
                        disabled={index === rows.length - 1}
                        icon={<ArrowDown size={13} />}
                        onClick={() => move(index, index + 1)}
                      />
                    </Tooltip>
                    <Tooltip title={t("features.settingsStepRemove")}>
                      <Button
                        type="text"
                        size="small"
                        danger
                        aria-label={t("features.settingsStepRemove")}
                        icon={<Trash2 size={13} />}
                        onClick={() => remove(field.name)}
                      />
                    </Tooltip>
                  </div>
                </div>

                <div className={styles.blockGrid}>
                  <Form.Item
                    name={[field.name, "mode"]}
                    label={t("features.settingsStepMode")}
                    tooltip={t("features.settingsStepModeHint")}
                    rules={[requiredRule(t("features.settingsStepModeRequired"))]}
                  >
                    <Select
                      options={FEATURE_STEP_MODES.map((mode) => ({
                        value: mode,
                        label: t(MODE_LABEL_KEYS[mode]),
                      }))}
                    />
                  </Form.Item>

                  <Form.Item
                    name={[field.name, "agent_role"]}
                    label={t("features.settingsStepAgentRole")}
                    tooltip={t("features.settingsStepAgentRoleHint")}
                  >
                    <Select
                      allowClear
                      placeholder={t("features.settingsStepAgentRolePlaceholder")}
                      options={choices.subagents.map((role) => ({
                        value: role,
                        label: role,
                      }))}
                    />
                  </Form.Item>

                  <Form.Item
                    name={[field.name, "gate"]}
                    label={t("features.settingsStepGate")}
                    tooltip={t("features.settingsStepGateHint")}
                    rules={[requiredRule(t("features.settingsStepGateRequired"))]}
                  >
                    <Select
                      options={FEATURE_STEP_GATES.map((gate) => ({
                        value: gate,
                        label: t(GATE_LABEL_KEYS[gate]),
                      }))}
                    />
                  </Form.Item>

                  <Form.Item
                    name={[field.name, "on_failure"]}
                    label={t("features.settingsStepOnFailure")}
                    tooltip={t("features.settingsStepOnFailureHint")}
                    rules={[
                      requiredRule(t("features.settingsStepOnFailureRequired")),
                    ]}
                  >
                    <Select
                      options={FEATURE_STEP_FAILURES.map((failure) => ({
                        value: failure,
                        label: t(FAILURE_LABEL_KEYS[failure]),
                      }))}
                    />
                  </Form.Item>

                  <Form.Item
                    name={[field.name, "max_parallel"]}
                    label={t("features.settingsStepMaxParallel")}
                    tooltip={t("features.settingsStepMaxParallelHint")}
                  >
                    <InputNumber
                      min={1}
                      style={{ width: "100%" }}
                      placeholder={t("features.settingsStepMaxParallelPlaceholder")}
                    />
                  </Form.Item>

                  <Form.Item
                    name={[field.name, "output", "name"]}
                    label={t("features.settingsStepOutputName")}
                    tooltip={t("features.settingsStepOutputNameHint")}
                    rules={[
                      requiredRule(t("features.settingsStepOutputNameRequired")),
                    ]}
                  >
                    <Input placeholder="bom_rows" autoComplete="off" />
                  </Form.Item>

                  <Form.Item
                    name={[field.name, "output", "schema"]}
                    label={t("features.settingsStepOutputSchema")}
                    tooltip={t("features.settingsStepOutputSchemaHint")}
                    rules={[
                      requiredRule(t("features.settingsStepOutputSchemaRequired")),
                    ]}
                  >
                    <AutoComplete
                      options={OUTPUT_SCHEMA_OPTIONS.map((value) => ({ value }))}
                      placeholder="table:12cols"
                    />
                  </Form.Item>

                  <div className={styles.blockWide}>
                    <Form.Item
                      name={[field.name, "inputs"]}
                      label={t("features.settingsStepInputs")}
                      extra={t("features.settingsStepInputsHint")}
                    >
                      <Select
                        mode="tags"
                        allowClear
                        tokenSeparators={[",", " "]}
                        placeholder={t("features.settingsStepInputsPlaceholder")}
                        options={artifactNamesBefore(steps, index).map(
                          (name) => ({ value: name, label: name }),
                        )}
                      />
                    </Form.Item>
                  </div>

                  <div className={styles.blockWide}>
                    <DeclaredListField
                      name={[field.name, "tools"]}
                      labelKey="features.settingsStepTools"
                      hintKey="features.settingsStepToolsHint"
                      inheritLabel={t("features.settingsStepToolsInherit")}
                      loading={false}
                      options={choices.tools.map((tool) => ({
                        value: tool.name,
                        label: `${tool.name} · ${tool.category}`,
                      }))}
                    />
                  </div>

                  {gated && (
                    <div className={styles.blockWide}>
                      <Form.Item
                        name={[field.name, "allow_edit"]}
                        valuePropName="checked"
                        label={t("features.settingsStepAllowEdit")}
                        tooltip={t("features.settingsStepAllowEditHint")}
                      >
                        <Checkbox>
                          {t("features.settingsStepAllowEditLabel")}
                        </Checkbox>
                      </Form.Item>
                    </div>
                  )}
                </div>

                {step?.gate === "validate" &&
                  step?.output?.schema !== undefined &&
                  step.output.schema !== "" &&
                  step.output.schema !== "object" && (
                    <Alert
                      type="warning"
                      showIcon
                      message={t("features.settingsStepValidateSchema")}
                    />
                  )}

                <Form.Item
                  name={[field.name, "prompt"]}
                  label={t("features.settingsStepPrompt")}
                  rules={[
                    requiredRule(t("features.settingsStepPromptRequired")),
                  ]}
                >
                  <Input.TextArea
                    className={styles.promptEditor}
                    autoSize={{ minRows: 3, maxRows: 12 }}
                  />
                </Form.Item>
              </article>
            );
          })}

          <Button
            className={styles.addFieldButton}
            icon={<Plus size={14} />}
            onClick={() => add(emptyStep())}
          >
            {t("features.settingsStepsAdd")}
          </Button>
        </div>
      )}
    </Form.List>
  );
}

/**
 * The step block as a collapsible settings block of the drawer.
 *
 * The step rows themselves are free to edit; the choices they borrow from the
 * caller's agent (the tool catalogue) are fetched on the first expand.
 */
export function FeatureStepsSection() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <section className={styles.settingsBlock}>
      <Collapse
        ghost
        className={styles.capabilityCollapse}
        onChange={(keys) =>
          setOpen(Array.isArray(keys) ? keys.length > 0 : Boolean(keys))
        }
        items={[
          {
            key: "steps",
            label: t("features.settingsSectionSteps"),
            children: (
              <>
                <div className={styles.blockHint}>
                  {t("features.settingsSectionStepsHint")}
                </div>
                <FeatureStepsFields open={open} />
              </>
            ),
          },
        ]}
      />
    </section>
  );
}
