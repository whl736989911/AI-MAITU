/**
 * 规则 — the soft rules a run follows besides its steps.
 *
 * Steps say what runs; rules say how. They are the things that hold across every
 * step — "check the amounts line by line" — and they are deliberately the humblest
 * part of the document: a plain ordered list of sentences, because that is what
 * they are, and because the caller's own overlay may outrank any of them.
 */

import { Button, Input } from "antd";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  MAX_RULES,
  moveItem,
  sectionProblems,
} from "../utils/workflowDocument";
import WorkflowSectionHeader from "./WorkflowSectionHeader";
import styles from "./FeatureWorkflowPanel.module.less";

export interface WorkflowRulesSectionProps {
  rules: readonly string[];
  problems: readonly string[];
  readOnly: boolean;
  onChange: (rules: string[]) => void;
}

export default function WorkflowRulesSection({
  rules,
  problems,
  readOnly,
  onChange,
}: WorkflowRulesSectionProps) {
  const { t } = useTranslation();

  return (
    <section className={styles.section}>
      <WorkflowSectionHeader
        title={t("features.workflow.rules.title")}
        hint={t("features.workflow.rules.hint")}
        problems={sectionProblems(problems, "rules")}
        action={
          readOnly ? undefined : (
            <Button
              size="small"
              icon={<Plus size={13} />}
              disabled={rules.length >= MAX_RULES}
              onClick={() => onChange([...rules, ""])}
            >
              {t("features.workflow.rules.add")}
            </Button>
          )
        }
      />

      {rules.length === 0 ? (
        <p className={styles.sectionEmpty}>
          {t("features.workflow.rules.empty")}
        </p>
      ) : (
        <div className={styles.ruleList}>
          {rules.map((rule, index) => (
            <div className={styles.ruleRow} key={index}>
              <Input
                value={rule}
                disabled={readOnly}
                aria-label={t("features.workflow.rules.rule", {
                  index: index + 1,
                })}
                placeholder={t("features.workflow.rules.placeholder")}
                onChange={(event) =>
                  onChange(
                    rules.map((item, position) =>
                      position === index ? event.target.value : item,
                    ),
                  )
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
                    onClick={() => onChange(moveItem(rules, index, index - 1))}
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<ArrowDown size={13} />}
                    disabled={index === rules.length - 1}
                    aria-label={t("features.workflow.moveDown", {
                      index: index + 1,
                    })}
                    onClick={() => onChange(moveItem(rules, index, index + 1))}
                  />
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<Trash2 size={13} />}
                    aria-label={t("features.workflow.rules.remove", {
                      index: index + 1,
                    })}
                    onClick={() =>
                      onChange(
                        rules.filter((_, position) => position !== index),
                      )
                    }
                  />
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
