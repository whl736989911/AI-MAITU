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
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  WORKFLOW_OUTPUT_FORMS,
  type WorkflowOutput,
} from "../../../api/modules/featureWorkflow";
import { moveItem, sectionProblems } from "../utils/workflowDocument";
import WorkflowSectionHeader from "./WorkflowSectionHeader";
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
  const { t } = useTranslation();

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

      {outputs.length === 0 ? (
        <p className={styles.sectionEmpty}>
          {t("features.workflow.outputs.empty")}
        </p>
      ) : (
        <div className={styles.cardList}>
          {outputs.map((output, index) => (
            <div className={styles.card} key={index}>
              <div className={styles.cardHeader}>
                <span className={styles.cardIndex}>{index + 1}</span>
                <Input
                  className={styles.grow}
                  value={output.name}
                  disabled={readOnly}
                  aria-label={t("features.workflow.outputs.name", {
                    index: index + 1,
                  })}
                  placeholder={t("features.workflow.outputs.namePlaceholder")}
                  onChange={(event) =>
                    setOutput(index, { name: event.target.value })
                  }
                />
                <Select
                  className={styles.typeSelect}
                  value={output.form}
                  disabled={readOnly}
                  aria-label={t("features.workflow.outputs.form", {
                    index: index + 1,
                  })}
                  options={WORKFLOW_OUTPUT_FORMS.map((form) => ({
                    value: form,
                    label: form,
                  }))}
                  onChange={(form) => setOutput(index, { form })}
                />
                <Input
                  className={styles.grow}
                  value={output.path ?? ""}
                  disabled={readOnly}
                  aria-label={t("features.workflow.outputs.path", {
                    index: index + 1,
                  })}
                  placeholder="outputs/report.md"
                  onChange={(event) =>
                    setOutput(index, { path: event.target.value })
                  }
                />
                {readOnly ? null : (
                  <>
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
                  </>
                )}
              </div>

              <div className={styles.fieldRow}>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>
                    {t("features.workflow.outputs.descZh")}
                  </span>
                  <Input
                    value={output.description?.zh ?? ""}
                    disabled={readOnly}
                    onChange={(event) =>
                      setOutput(index, {
                        description: {
                          zh: event.target.value,
                          en: output.description?.en ?? "",
                        },
                      })
                    }
                  />
                </label>
                <label className={styles.field}>
                  <span className={styles.fieldLabel}>
                    {t("features.workflow.outputs.descEn")}
                  </span>
                  <Input
                    value={output.description?.en ?? ""}
                    disabled={readOnly}
                    onChange={(event) =>
                      setOutput(index, {
                        description: {
                          zh: output.description?.zh ?? "",
                          en: event.target.value,
                        },
                      })
                    }
                  />
                </label>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
