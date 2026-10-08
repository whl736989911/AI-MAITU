export interface HitlActionRequest {
  name: string;
  args?: Record<string, unknown>;
  description?: string;
}

export interface HitlReviewConfig {
  action_name: string;
  allowed_decisions: string[];
}

export interface HitlPendingPayload extends Record<string, unknown> {
  pending_id?: string;
  action_requests: HitlActionRequest[];
  review_configs?: HitlReviewConfig[];
}

/** Unwrap LangGraph v2 Interrupt envelopes used in HITL stream payloads. */
export function normalizeHitlRequest(raw: unknown): HitlPendingPayload | null {
  let current: unknown = raw;
  for (let depth = 0; depth < 3; depth += 1) {
    if (!current || typeof current !== "object" || Array.isArray(current)) {
      break;
    }
    const value = current as Record<string, unknown>;
    if (Array.isArray(value.action_requests)) {
      return {
        action_requests: value.action_requests
          .filter((item): item is Record<string, unknown> =>
            Boolean(item && typeof item === "object" && !Array.isArray(item)),
          )
          .map((item) => {
            let args = item.args;
            if (typeof args === "string") {
              try {
                args = JSON.parse(args);
              } catch {
                args = {};
              }
            }
            return {
              ...item,
              name: typeof item.name === "string" ? item.name : "tool",
              args:
                args && typeof args === "object" && !Array.isArray(args)
                  ? (args as Record<string, unknown>)
                  : {},
            };
          }),
        review_configs: Array.isArray(value.review_configs)
          ? (value.review_configs as HitlReviewConfig[])
          : undefined,
        pending_id:
          typeof value.pending_id === "string" ? value.pending_id : undefined,
      };
    }
    current = value.value;
  }
  return null;
}

/** Tool whose HITL pause is a question for the user, not an approval. */
export const ASK_USER_TOOL_NAME = "ask_user_question";

export interface AskOption {
  label: string;
  description?: string;
}

export interface AskQuestion {
  question: string;
  header?: string;
  options?: AskOption[];
  multi_select?: boolean;
}

/** Extract the `questions` payload from an `ask_user_question` pause. */
export function extractAskQuestions(
  actions: HitlActionRequest[] | undefined,
): AskQuestion[] {
  if (!actions?.length) return [];
  for (const action of actions) {
    if (action.name !== ASK_USER_TOOL_NAME) continue;
    const raw = action.args?.questions;
    if (!Array.isArray(raw)) continue;
    return raw
      .filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
      .map((item) => ({
        question: typeof item.question === "string" ? item.question : "",
        header: typeof item.header === "string" ? item.header : undefined,
        multi_select: item.multi_select === true,
        options: Array.isArray(item.options)
          ? item.options
              .filter((opt): opt is Record<string, unknown> =>
                Boolean(opt && typeof opt === "object" && !Array.isArray(opt)),
              )
              .map((opt) => ({
                label: typeof opt.label === "string" ? opt.label : "",
                description:
                  typeof opt.description === "string"
                    ? opt.description
                    : undefined,
              }))
              .filter((opt) => opt.label)
          : [],
      }))
      .filter((q) => q.question);
  }
  return [];
}

export function isAskHitl(actions: HitlActionRequest[] | undefined): boolean {
  return Boolean(actions?.some((a) => a.name === ASK_USER_TOOL_NAME));
}
