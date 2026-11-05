import type { KnowledgeMetadataField } from "../../api/modules/knowledgeBases";

/**
 * Keep only values for fields the current schema still defines.
 *
 * A document can retain values from fields an owner later removed; sending
 * those stale keys back makes the API reject the whole update as unknown.
 */
export function metadataValuesForSchema(
  values: Record<string, unknown>,
  schema: KnowledgeMetadataField[],
): Record<string, unknown> {
  const keys = new Set(schema.map((field) => field.key));
  return Object.fromEntries(
    Object.entries(values).filter(([key]) => keys.has(key)),
  );
}
