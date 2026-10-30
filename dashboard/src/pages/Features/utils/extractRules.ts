/**
 * Pure helpers for the extraction-rules editor.
 *
 * The editor works on flat string inputs (labels as comma-separated text) and
 * converts them to the version-2 rules document the server validates. Keeping
 * that conversion here means it is testable without rendering the form.
 */

import type {
  FeatureDataLocalizedText,
  FeatureDataSchema,
  FeatureExtractProfile,
  FeatureExtractRule,
  FeatureExtractRules,
} from "../../../api/modules/featureData";

export type ExtractSource = "label" | "dimensions";
export type ExtractPick = "max_outer" | "min_inner" | "text";

export interface ExtractFieldDraft {
  /** Stable key for React lists; not sent to the server. */
  id: string;
  fieldKey: string;
  source: ExtractSource;
  labels: string;
  pattern: string;
  unitKg: boolean;
  pick: ExtractPick;
  limit: string;
}

export interface ExtractProfileDraft {
  id: string;
  name: string;
  nameTranslations?: FeatureDataLocalizedText;
  labelsAny: string;
  filenamePattern: string;
  fields: ExtractFieldDraft[];
}

let draftSeq = 0;

export function newDraftId(prefix: string): string {
  draftSeq += 1;
  return `${prefix}-${draftSeq}`;
}

export function splitList(text: string): string[] {
  return text
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function profileName(
  name: FeatureExtractProfile["name"],
  locale: string,
): string {
  if (typeof name === "string") return name;
  const zh = name.zh ?? "";
  const en = name.en ?? "";
  return (locale.startsWith("en") ? en || zh : zh || en) || "default";
}

export function draftsFromRules(
  rules: FeatureExtractRules | null,
  locale: string,
): ExtractProfileDraft[] {
  if (!rules?.profiles?.length) return [];
  return rules.profiles.map((profile) => ({
    id: newDraftId("profile"),
    name: profileName(profile.name, locale),
    nameTranslations:
      typeof profile.name === "string" ? undefined : { ...profile.name },
    labelsAny: (profile.when?.labels_any ?? []).join(", "),
    filenamePattern: profile.when?.filename_pattern ?? "",
    fields: Object.entries(profile.fields ?? {}).map(([key, rule]) => ({
      id: newDraftId("field"),
      fieldKey: key,
      source: rule.from === "dimensions" ? "dimensions" : "label",
      labels: (rule.labels ?? []).join(", "),
      pattern: rule.pattern ?? "",
      unitKg: rule.unit === "kg",
      pick: rule.pick ?? (rule.limit ? "text" : "max_outer"),
      limit: rule.limit ? String(rule.limit) : "",
    })),
  }));
}

export function rulesFromDrafts(
  drafts: ExtractProfileDraft[],
  locale: string,
): FeatureExtractRules {
  return {
    version: 2,
    profiles: drafts.map((draft) => {
      const displayName = draft.name.trim() || locale;
      let name: FeatureExtractProfile["name"] = displayName;
      if (draft.nameTranslations) {
        name = { ...draft.nameTranslations };
        if (displayName !== profileName(name, locale)) {
          name[locale.startsWith("en") ? "en" : "zh"] = displayName;
        }
      }
      const fields: Record<string, FeatureExtractRule> = {};
      for (const field of draft.fields) {
        if (!field.fieldKey) continue;
        const rule: FeatureExtractRule = {};
        if (field.source === "dimensions") {
          rule.from = "dimensions";
          if (field.pick === "text") {
            const limit = Number.parseInt(field.limit, 10);
            if (Number.isFinite(limit) && limit > 0) rule.limit = limit;
            else rule.pick = "text";
          } else {
            rule.pick = field.pick;
          }
        } else {
          const labels = splitList(field.labels);
          if (labels.length) rule.labels = labels;
          if (field.pattern.trim()) rule.pattern = field.pattern.trim();
          if (field.unitKg) rule.unit = "kg";
        }
        fields[field.fieldKey] = rule;
      }
      const when: FeatureExtractProfile["when"] = {};
      const labelsAny = splitList(draft.labelsAny);
      if (labelsAny.length) when.labels_any = labelsAny;
      if (draft.filenamePattern.trim())
        when.filename_pattern = draft.filenamePattern.trim();
      return {
        name,
        when,
        fields,
      };
    }),
  };
}

/** Why a draft cannot be saved yet, or ``null`` when it can. */
export function draftProblem(
  drafts: ExtractProfileDraft[],
  schema: FeatureDataSchema | null,
): "empty" | "noFields" | null {
  if (!drafts.length) return "empty";
  for (const draft of drafts) {
    if (!draft.fields.length) return "noFields";
    for (const field of draft.fields) {
      if (!field.fieldKey) return "noFields";
    }
  }
  void schema;
  return null;
}

/** Field keys declared by the schema, in declaration order. */
export function schemaFieldKeys(schema: FeatureDataSchema | null): string[] {
  const keys: string[] = [];
  for (const object of schema?.objects ?? []) {
    for (const field of object.fields ?? []) {
      if (field.key && !keys.includes(field.key)) keys.push(field.key);
    }
  }
  return keys;
}
