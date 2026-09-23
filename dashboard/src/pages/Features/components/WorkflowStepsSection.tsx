/**
 * 步骤 — the fixed steps of a run, and the editor for the one being written.
 *
 * The array's order *is* the flow (step 2 runs after step 1), so the left side is
 * an outline a reader scans top to bottom and can drag into the order they mean,
 * and the right side is the whole of one step — the outline is a way to pick a step
 * out, not a summary to edit in.
 *
 * What the outline says about a step without opening it is what decides whether a
 * run is safe to start: its position, its name, whether it stops for a person
 * (``gate``), and whether anything is wrong with it. Those problems are the same
 * strings the refusal list shows, attributed to their own step by their own wording
 * (``problemOwner``), so a refusal marks the step it is about instead of leaving the
 * author to count.
 *
 * ``depends_on`` may only name *earlier* steps — a forward reference is a cycle the
 * run cannot follow — so the field offers exactly the ids above this step and the
 * server says the same thing if one is written another way.
 */

import { useRef, useState } from "react";
import { Button, Input, Popconfirm, Radio, Select, Tooltip } from "antd";
import { ChevronDown, Copy, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  type WorkflowStep,
  type WorkflowStepGate,
} from "../../../api/modules/featureWorkflow";
import {
  MAX_STEPS,
  moveItem,
  sectionProblems,
  slugifyStepId,
  stepProblems,
  uniqueStepId,
} from "../utils/workflowDocument";
import WorkflowSectionHeader from "./WorkflowSectionHeader";
import styles from "./FeatureWorkflowPanel.module.less";

export interface WorkflowStepsSectionProps {
  steps: readonly WorkflowStep[];
  /** Every current problem — this section keeps each step's own. */
  problems: readonly string[];
  /** An active definition must name every step and say what it does. */
  active: boolean;
  readOnly: boolean;
  onChange: (steps: WorkflowStep[]) => void;
}

export default function WorkflowStepsSection({
  steps,
  problems,
  active,
  readOnly,
  onChange,
}: WorkflowStepsSectionProps) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState(0);
  const [collapsed, setCollapsed] = useState<readonly number[]>([]);
  const [dragging, setDragging] = useState<number | null>(null);
  const [dropTarget, setDropTarget] = useState<number | null>(null);
  const promptRef = useRef<HTMLTextAreaElement | null>(null);

  const index = steps.length === 0 ? -1 : Math.min(selected, steps.length - 1);
  const step = index >= 0 ? steps[index] : null;
  const selectedProblems = index >= 0 ? stepProblems(problems, index) : [];
  const usedIds = new Set(
    steps.map((item) => item.id ?? "").filter((id) => id !== ""),
  );

  const updateStep = (patch: Partial<WorkflowStep>) => {
    if (index < 0) return;
    onChange(
      steps.map((item, position) =>
        position === index ? { ...item, ...patch } : item,
      ),
    );
  };

  const addStep = () => {
    // A step nobody has named yet still needs a legal id, or an active definition
    // would refuse it for a reason its author has not been asked about yet.
    const id = uniqueStepId(slugifyStepId("", steps.length + 1), usedIds);
    onChange([...steps, { id, name: "" }]);
    setSelected(steps.length);
  };

  const duplicateStep = () => {
    if (!step) return;
    const base = slugifyStepId(`${step.id || step.name || "step"}_copy`);
    const copy: WorkflowStep = { ...step, id: uniqueStepId(base, usedIds) };
    const next = [...steps];
    next.splice(index + 1, 0, copy);
    onChange(next);
    setSelected(index + 1);
  };

  const removeStep = () => {
    if (index < 0) return;
    onChange(steps.filter((_, position) => position !== index));
    setSelected(Math.max(0, index - 1));
  };

  const toggleCollapsed = (position: number) => {
    setCollapsed((previous) =>
      previous.includes(position)
        ? previous.filter((item) => item !== position)
        : [...previous, position],
    );
  };

  const handleDrop = (position: number) => {
    if (dragging !== null && dragging !== position) {
      onChange(moveItem(steps, dragging, position));
      setSelected(position);
    }
    setDragging(null);
    setDropTarget(null);
  };

  const insertPlaceholder = (token: string) => {
    if (!step) return;
    const text = step.prompt ?? "";
    const area = promptRef.current;
    const start = area?.selectionStart ?? text.length;
    const end = area?.selectionEnd ?? start;
    updateStep({ prompt: `${text.slice(0, start)}${token}${text.slice(end)}` });
    const caret = start + token.length;
    requestAnimationFrame(() => {
      area?.focus();
      area?.setSelectionRange(caret, caret);
    });
  };

  return (
    <section className={styles.section}>
      <WorkflowSectionHeader
        title={t("features.workflow.steps.title")}
        hint={t("features.workflow.steps.hint")}
        problems={sectionProblems(problems, "steps")}
        action={
          readOnly ? undefined : (
            <Button
              size="small"
              icon={<Plus size={13} />}
              disabled={steps.length >= MAX_STEPS}
              onClick={addStep}
            >
              {t("features.workflow.steps.add")}
            </Button>
          )
        }
      />

      <div className={styles.steps}>
        <div className={styles.stepOutline}>
          {steps.length === 0 ? (
            <p className={styles.sectionEmpty}>
              {t("features.workflow.steps.empty")}
            </p>
          ) : (
            steps.map((item, position) => {
              const rowProblems = stepProblems(problems, position);
              const isCollapsed = collapsed.includes(position);
              const isDropTarget =
                dragging !== null &&
                dropTarget === position &&
                dragging !== position;
              return (
                <div
                  key={position}
                  role="button"
                  tabIndex={0}
                  aria-pressed={position === index}
                  draggable={!readOnly}
                  className={[
                    styles.stepRow,
                    position === index ? styles.stepRowActive : "",
                    isDropTarget ? styles.stepRowDrop : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  onClick={() => setSelected(position)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelected(position);
                    }
                  }}
                  onDragStart={() => setDragging(position)}
                  onDragOver={(event) => {
                    event.preventDefault();
                    setDropTarget(position);
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    handleDrop(position);
                  }}
                  onDragEnd={() => {
                    setDragging(null);
                    setDropTarget(null);
                  }}
                >
                  <span className={styles.stepNumber}>{position + 1}</span>
                  <div className={styles.stepText}>
                    <span className={styles.stepName}>
                      {item.name.trim() ||
                        t("features.workflow.steps.untitled")}
                    </span>
                    {isCollapsed ? null : (
                      <span className={styles.stepMeta}>
                        {item.id
                          ? `#${item.id}`
                          : t("features.workflow.steps.noId")}
                        {item.depends_on && item.depends_on.length > 0
                          ? ` · ${t(
                              "features.workflow.steps.after",
                            )} ${item.depends_on.join(", ")}`
                          : ""}
                      </span>
                    )}
                  </div>
                  {item.gate === "confirm" ? (
                    <span className={styles.stepGate}>
                      {t("features.workflow.steps.gateConfirm")}
                    </span>
                  ) : null}
                  {rowProblems.length > 0 ? (
                    <Tooltip
                      title={
                        <ul className={styles.problemList}>
                          {rowProblems.map((problem) => (
                            <li key={problem}>{problem}</li>
                          ))}
                        </ul>
                      }
                    >
                      <span className={styles.stepWarn} role="status">
                        {rowProblems.length}
                      </span>
                    </Tooltip>
                  ) : null}
                  <button
                    type="button"
                    className={styles.stepToggle}
                    aria-expanded={!isCollapsed}
                    aria-label={
                      isCollapsed
                        ? t("features.workflow.steps.expand")
                        : t("features.workflow.steps.collapse")
                    }
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleCollapsed(position);
                    }}
                  >
                    <ChevronDown
                      size={14}
                      className={
                        isCollapsed ? styles.stepToggleClosed : undefined
                      }
                    />
                  </button>
                </div>
              );
            })
          )}
        </div>

        <div className={styles.stepEditor}>
          {step === null ? (
            <p className={styles.sectionEmpty}>
              {t("features.workflow.steps.pick")}
            </p>
          ) : (
            <>
              <div className={styles.editorHeader}>
                <span className={styles.editorTitle}>
                  {t("features.workflow.steps.editorTitle", {
                    index: index + 1,
                  })}
                </span>
                {readOnly ? null : (
                  <span className={styles.editorActions}>
                    <Button
                      size="small"
                      icon={<Copy size={13} />}
                      onClick={duplicateStep}
                    >
                      {t("features.workflow.steps.duplicate")}
                    </Button>
                    <Popconfirm
                      title={t("features.workflow.steps.deleteConfirm")}
                      onConfirm={removeStep}
                    >
                      <Button size="small" danger icon={<Trash2 size={13} />}>
                        {t("common.delete")}
                      </Button>
                    </Popconfirm>
                  </span>
                )}
              </div>

              {selectedProblems.length > 0 ? (
                <ul className={styles.editorProblems}>
                  {selectedProblems.map((problem) => (
                    <li key={problem}>{problem}</li>
                  ))}
                </ul>
              ) : null}

              <label className={styles.field}>
                <span className={styles.fieldLabel}>
                  {t("features.workflow.steps.name")}
                  {active ? (
                    <span className={styles.requiredMark}> *</span>
                  ) : null}
                </span>
                <Input
                  value={step.name}
                  disabled={readOnly}
                  placeholder={t("features.workflow.steps.namePlaceholder")}
                  onChange={(event) => updateStep({ name: event.target.value })}
                />
              </label>

              <label className={styles.field}>
                <span className={styles.fieldLabel}>
                  {t("features.workflow.steps.id")}
                  {active ? (
                    <span className={styles.requiredMark}> *</span>
                  ) : null}
                </span>
                <span className={styles.fieldRow}>
                  <Input
                    className={styles.grow}
                    value={step.id ?? ""}
                    disabled={readOnly}
                    placeholder="extract"
                    onChange={(event) => updateStep({ id: event.target.value })}
                  />
                  <Button
                    size="small"
                    disabled={readOnly}
                    onClick={() =>
                      updateStep({
                        id: uniqueStepId(
                          slugifyStepId(step.name, index + 1),
                          usedIds,
                        ),
                      })
                    }
                  >
                    {t("features.workflow.steps.generateId")}
                  </Button>
                </span>
                <span className={styles.fieldHint}>
                  {t("features.workflow.steps.idHint")}
                </span>
              </label>

              <div className={styles.field}>
                <span className={styles.fieldLabel}>
                  {t("features.workflow.steps.prompt")}
                  {active ? (
                    <span className={styles.requiredMark}> *</span>
                  ) : null}
                </span>
                <span className={styles.promptTools}>
                  <Button
                    size="small"
                    disabled={readOnly}
                    onClick={() => insertPlaceholder("{{inputs}}")}
                  >
                    {t("features.workflow.steps.insertInputs")}
                  </Button>
                  <Button
                    size="small"
                    disabled={readOnly}
                    onClick={() => insertPlaceholder("{{inputs_json}}")}
                  >
                    {t("features.workflow.steps.insertInputsJson")}
                  </Button>
                </span>
                <textarea
                  ref={promptRef}
                  className={styles.promptArea}
                  value={step.prompt ?? ""}
                  disabled={readOnly}
                  spellCheck={false}
                  aria-label={t("features.workflow.steps.prompt")}
                  placeholder={t("features.workflow.steps.promptPlaceholder")}
                  onChange={(event) =>
                    updateStep({ prompt: event.target.value })
                  }
                />
                <span className={styles.fieldHint}>
                  {t("features.workflow.steps.promptHint")}
                </span>
              </div>

              <label className={styles.field}>
                <span className={styles.fieldLabel}>
                  {t("features.workflow.steps.dependsOn")}
                </span>
                <Select
                  mode="multiple"
                  allowClear
                  value={step.depends_on ?? []}
                  disabled={readOnly}
                  placeholder={t(
                    "features.workflow.steps.dependsOnPlaceholder",
                  )}
                  options={steps
                    .slice(0, index)
                    .map((earlier, position) => ({
                      value: earlier.id ?? "",
                      label: `${position + 1}. ${
                        earlier.name.trim() || earlier.id || ""
                      }`,
                    }))
                    .filter((option) => option.value !== "")}
                  onChange={(values) => updateStep({ depends_on: values })}
                />
                <span className={styles.fieldHint}>
                  {t("features.workflow.steps.dependsOnHint")}
                </span>
              </label>

              {(
                [
                  ["skills", t("features.workflow.steps.skills")],
                  ["subagents", t("features.workflow.steps.subagents")],
                  ["tools", t("features.workflow.steps.tools")],
                ] as const
              ).map(([key, label]) => (
                <label className={styles.field} key={key}>
                  <span className={styles.fieldLabel}>{label}</span>
                  <Select
                    mode="tags"
                    allowClear
                    value={step[key] ?? []}
                    disabled={readOnly}
                    tokenSeparators={[",", " "]}
                    placeholder={t(
                      "features.workflow.steps.nameListPlaceholder",
                    )}
                    onChange={(values) => updateStep({ [key]: values })}
                  />
                </label>
              ))}

              <div className={styles.field}>
                <span className={styles.fieldLabel}>
                  {t("features.workflow.steps.gate")}
                </span>
                <Radio.Group
                  value={step.gate ?? "auto"}
                  disabled={readOnly}
                  onChange={(event) =>
                    updateStep({ gate: event.target.value as WorkflowStepGate })
                  }
                >
                  <Radio value="auto">
                    {t("features.workflow.steps.gateAuto")}
                  </Radio>
                  <Radio value="confirm">
                    {t("features.workflow.steps.gateConfirm")}
                  </Radio>
                </Radio.Group>
                <span className={styles.fieldHint}>
                  {t("features.workflow.steps.gateHint")}
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
