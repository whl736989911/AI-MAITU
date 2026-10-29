import { Check, ChevronRight, FileText } from "lucide-react";
import { useTranslation } from "react-i18next";
import styles from "./FileChangesPreview.module.less";

type DiffKind = "context" | "add" | "remove";
interface ChangeFixture {
  path: string;
  status: "A" | "M" | "D";
  lines: { kind: DiffKind; text: string }[];
}

// Read-only design samples; these are not workspace or Git changes.
const fixtures: ChangeFixture[] = [
  {
    path: "onboarding/guide.md",
    status: "A",
    lines: [
      { kind: "add", text: "# 新员工入职指南" },
      { kind: "add", text: "" },
      { kind: "add", text: "- 完成账号与设备配置" },
      { kind: "add", text: "- 与导师确认首周目标" },
    ],
  },
  {
    path: "onboarding/checklist.md",
    status: "M",
    lines: [
      { kind: "context", text: "- [x] 完成入职前准备" },
      { kind: "remove", text: "- [ ] 首周安排待补充" },
      { kind: "add", text: "- [x] 确认首周安排与责任人" },
      { kind: "add", text: "- [ ] 完成首周回访" },
    ],
  },
  {
    path: "onboarding/legacy.md",
    status: "D",
    lines: [
      { kind: "remove", text: "# 旧版入职说明" },
      { kind: "remove", text: "请参考旧版流程。" },
    ],
  },
];
const changes = fixtures.map((file) => {
  let oldLine = 1;
  let newLine = 1;
  return {
    ...file,
    added: file.lines.filter((line) => line.kind === "add").length,
    removed: file.lines.filter((line) => line.kind === "remove").length,
    lines: file.lines.map((line) => ({
      ...line,
      oldLine: line.kind === "add" ? "" : oldLine++,
      newLine: line.kind === "remove" ? "" : newLine++,
    })),
  };
});
const totals = changes.reduce(
  (sum, file) => ({
    added: sum.added + file.added,
    removed: sum.removed + file.removed,
  }),
  { added: 0, removed: 0 },
);

export default function FileChangesPreview() {
  const { t } = useTranslation();
  const statusLabel = {
    A: t("common.create"),
    M: t("common.edit"),
    D: t("common.delete"),
  };
  return (
    <details className={styles.card} open>
      <summary className={styles.header}>
        <Check size={14} className={styles.complete} aria-hidden="true" />
        <strong>{t("chat.editFileCard", { count: changes.length })}</strong>
        <span className={styles.stats}>
          <span>+{totals.added}</span>
          <span>−{totals.removed}</span>
        </span>
        <span className={styles.sample}>
          {t("interfacePreview.chatFlow.demo")}
        </span>
        <ChevronRight size={14} className={styles.chevron} aria-hidden="true" />
      </summary>
      <div className={styles.files}>
        {changes.map((file) => (
          <details key={file.path} className={styles.file}>
            <summary className={styles.fileRow} title={file.path}>
              <ChevronRight
                size={12}
                className={styles.chevron}
                aria-hidden="true"
              />
              <FileText size={14} aria-hidden="true" />
              <span className={styles.path}>
                <span>onboarding/</span>
                <strong>{file.path.split("/").at(-1)}</strong>
              </span>
              <span
                className={styles.status}
                data-status={file.status}
                title={statusLabel[file.status]}
              >
                {file.status}
              </span>
              <span className={styles.stats}>
                <span>+{file.added}</span>
                <span>−{file.removed}</span>
              </span>
            </summary>
            <div
              className={styles.diff}
              tabIndex={0}
              aria-label={`${file.path} diff`}
            >
              {file.lines.map((line, index) => (
                <div key={index} className={styles.line} data-kind={line.kind}>
                  <span className={styles.lineNumber} aria-hidden="true">
                    {line.oldLine}
                  </span>
                  <span className={styles.lineNumber} aria-hidden="true">
                    {line.newLine}
                  </span>
                  <span className={styles.sign} aria-hidden="true">
                    {line.kind === "add"
                      ? "+"
                      : line.kind === "remove"
                      ? "−"
                      : " "}
                  </span>
                  <code>{line.text || " "}</code>
                </div>
              ))}
            </div>
          </details>
        ))}
      </div>
    </details>
  );
}
