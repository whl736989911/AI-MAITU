/**
 * 输出 — what a run hands back when it is done.
 *
 * An output is a promise about the run's result: what it is called, what form it
 * takes and, for anything written to disk, where it lands. Declaring them is what
 * lets a run be judged finished rather than merely stopped — so the rows are read
 * as an ordered list of deliverables, and the form is chosen from the four the run
 * knows how to produce (``markdown``/``json``/``text``/``file``).
 */

import { Button, Input, Select } from "antd";
import { ArrowDown, ArrowUp, FileText, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  WORKFLOW_OUTPUT_FORMS,
  type WorkflowOutput,
} from "../../../api/modules/featureWorkflow";
import { moveItem, sectionProblems } from "../utils/workflowDocument";
import WorkflowSectionHeader from "./WorkflowSectionHeader";
import chatStyles from "../../Chat/components/WorkflowOutputCard.module.less";
import styles from "./FeatureWorkflowPanel.module.less";

export interface WorkflowOutputsSectionProps {
  outputs: readonly WorkflowOutput[];
  problems: readonly string[];
  readOnly: boolean;
  onChange: (outputs: WorkflowOutput[]) => void;
}

export default function WorkflowOutputsSection({
  outputs,
  problems,
  readOnly,
  onChange,
}: WorkflowOutputsSectionProps) {
  const { t, i18n } = useTranslation();
  const language = i18n.language?.startsWith("zh") ? "zh" : "en";
  const otherLanguage = language === "zh" ? "en" : "zh";

  const setOutput = (index: number, patch: Partial<WorkflowOutput>) => {
    onChange(
      outputs.map((output, position) =>
        position === index ? { ...output, ...patch } : output,
      ),
    );
  };

  return (
    <section className={styles.section}>
      <WorkflowSectionHeader
        title={t("features.workflow.outputs.title")}
        hint={t("features.workflow.outputs.hint")}
        problems={sectionProblems(problems, "outputs")}
        action={
          readOnly ? undefined : (
            <Button
              size="small"
              icon={<Plus size={13} />}
              onClick={() =>
                onChange([...outputs, { name: "", form: "markdown" }])
              }
            >
              {t("features.workflow.outputs.add")}
            </Button>
          )
        }
      />
      <p className={styles.previewCardCaption}>
        {t("features.workflow.outputs.preview")}
      </p>
      <div className={chatStyles.card}>
        <div className={chatStyles.header}>
          <span className={chatStyles.title}>
            {t("chat.workflow.outputTitle")}
          </span>
          <span className={chatStyles.count}>
            {t("chat.workflow.outputCount", { count: outputs.length })}
          </span>
        </div>
        {outputs.length === 0 ? (
          <p className={styles.sectionEmpty}>
            {t("features.workflow.outputs.empty")}
          </p>
        ) : (
          <ul className={chatStyles.fileList}>
            {outputs.map((output, index) => (
              <li className={styles.previewOutputRow} key={index}>
                <FileText
                  className={chatStyles.fileIcon}
                  size={16}
                  aria-hidden="true"
                />
                <div className={styles.previewOutputMain}>
                  <label className={styles.field}>
                    <span className={styles.fieldLabel}>
                      {t("features.workflow.outputs.name", {
                        index: index + 1,
                      })}
                    </span>
                    <Input
                      value={output.name}
                      disabled={readOnly}
                      placeholder={t(
                        "features.workflow.outputs.namePlaceholder",
                      )}
                      onChange={(event) =>
                        setOutput(index, { name: event.target.value })
                      }
                    />
                  </label>
                  <label className={styles.field}>
                    <span className={styles.fieldLabel}>
                      {t(
                        language === "zh"
                          ? "features.workflow.outputs.descZh"
                          : "features.workflow.outputs.descEn",
                      )}
                    </span>
                    <Input
                      value={output.description?.[language] ?? ""}
                      disabled={readOnly}
                      onChange={(event) =>
                        setOutput(index, {
                          description: {
                            zh: output.description?.zh ?? "",
                            en: output.description?.en ?? "",
                            [language]: event.target.value,
                            [otherLanguage]:
                              (output.description?.[otherLanguage] ?? "") ===
                              (output.description?.[language] ?? "")
                                ? event.target.value
                                : output.description?.[otherLanguage] ?? "",
                          },
                        })
                      }
                    />
                  </label>
                  <div className={styles.previewOutputMeta}>
                    <span>
                      {t(
                        `features.workflow.outputs.formOptions.${output.form}`,
                      )}
                    </span>
                    {output.path ? <span>{output.path}</span> : null}
                  </div>
                  <details className={styles.previewDetails}>
                    <summary className={styles.previewDetailsSummary}>
                      {t("features.workflow.outputs.details")}
                    </summary>
                    <div className={styles.previewDetailsBody}>
                      <label className={styles.field}>
                        <span className={styles.fieldLabel}>
                          {t("features.workflow.outputs.form", {
                            index: index + 1,
                          })}
                        </span>
                        <Select
                          value={output.form}
                          disabled={readOnly}
                          options={WORKFLOW_OUTPUT_FORMS.map((form) => ({
                            value: form,
                            label: t(
                              `features.workflow.outputs.formOptions.${form}`,
                            ),
                          }))}
                          onChange={(form) => setOutput(index, { form })}
                        />
                      </label>
                      <label className={styles.field}>
                        <span className={styles.fieldLabel}>
                          {t("features.workflow.outputs.path", {
                            index: index + 1,
                          })}
                        </span>
                        <Input
                          value={output.path ?? ""}
                          disabled={readOnly}
                          placeholder="outputs/report.md"
                          onChange={(event) =>
                            setOutput(index, { path: event.target.value })
                          }
                        />
                        <span className={styles.fieldHint}>
                          {t("features.workflow.outputs.pathHint")}
                        </span>
                      </label>
                      <label className={styles.field}>
                        <span className={styles.fieldLabel}>
                          {t(
                            otherLanguage === "zh"
                              ? "features.workflow.outputs.descZh"
                              : "features.workflow.outputs.descEn",
                          )}
                        </span>
                        <Input
                          value={output.description?.[otherLanguage] ?? ""}
                          disabled={readOnly}
                          onChange={(event) =>
                            setOutput(index, {
                              description: {
                                zh: output.description?.zh ?? "",
                                en: output.description?.en ?? "",
                                [otherLanguage]: event.target.value,
                              },
                            })
                          }
                        />
                      </label>
                    </div>
                  </details>
                </div>
                {readOnly ? null : (
                  <span className={styles.previewFieldActions}>
                    <Button
                      type="text"
                      size="small"
                      icon={<ArrowUp size={13} />}
                      disabled={index === 0}
                      aria-label={t("features.workflow.moveUp", {
                        index: index + 1,
                      })}
                      onClick={() =>
                        onChange(moveItem(outputs, index, index - 1))
                      }
                    />
                    <Button
                      type="text"
                      size="small"
                      icon={<ArrowDown size={13} />}
                      disabled={index === outputs.length - 1}
                      aria-label={t("features.workflow.moveDown", {
                        index: index + 1,
                      })}
                      onClick={() =>
                        onChange(moveItem(outputs, index, index + 1))
                      }
                    />
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<Trash2 size={13} />}
                      aria-label={t("features.workflow.outputs.remove", {
                        index: index + 1,
                      })}
                      onClick={() =>
                        onChange(
                          outputs.filter((_, position) => position !== index),
                        )
                      }
                    />
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
