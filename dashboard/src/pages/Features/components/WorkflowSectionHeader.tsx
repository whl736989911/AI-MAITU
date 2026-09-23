/**
 * A section's heading — the four sections of the workflow look and read alike.
 *
 * The title, one line saying what the section is for, and the problems that belong
 * to it: a problem is attached to the section it is about, so the count is beside
 * the heading a reader is already looking at rather than only in the refusal list
 * at the top of the page. The words themselves stay in the list as well — nothing
 * here is the only place a problem is said.
 */

import type { ReactNode } from "react";
import { Tooltip } from "antd";

import styles from "./FeatureWorkflowPanel.module.less";

export interface WorkflowSectionHeaderProps {
  title: string;
  hint: string;
  /** The problems of this section, as ``sectionProblems``/``stepProblems`` left them. */
  problems: readonly string[];
  /** The section's own action (usually "add"), placed with the heading. */
  action?: ReactNode;
}

export default function WorkflowSectionHeader({
  title,
  hint,
  problems,
  action,
}: WorkflowSectionHeaderProps) {
  return (
    <div className={styles.sectionHeader}>
      <div className={styles.sectionHeading}>
        <span className={styles.sectionTitle}>{title}</span>
        {problems.length > 0 ? (
          <Tooltip
            title={
              <ul className={styles.problemList}>
                {problems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            }
          >
            <span className={styles.sectionBadge} role="status">
              {problems.length}
            </span>
          </Tooltip>
        ) : null}
        {action}
      </div>
      <p className={styles.sectionHint}>{hint}</p>
    </div>
  );
}
