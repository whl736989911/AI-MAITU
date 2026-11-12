/**
 * Whether this assistant group is the live in-progress turn.
 *
 * After the user sends a new message, the previous assistant group is still
 * `lastAssistantGroupIndex` until the model replies — it must NOT be treated
 * as live, or its process summary re-expands and pushes the new user bubble
 * off-screen.
 */
export function isLiveAssistantTurn(opts: {
  isStreaming: boolean;
  groupIndex: number;
  lastAssistantGroupIndex: number;
  lastUserGroupIndex: number;
}): boolean {
  const {
    isStreaming,
    groupIndex,
    lastAssistantGroupIndex,
    lastUserGroupIndex,
  } = opts;
  return (
    isStreaming &&
    groupIndex === lastAssistantGroupIndex &&
    groupIndex > lastUserGroupIndex
  );
}

/** Keep live process state on the selected current assistant group only. */
export function isAssistantProcessLive(opts: {
  isTurnInProgress: boolean;
  afterLatestUser: boolean;
  hasAnswerText: boolean;
}): boolean {
  return opts.isTurnInProgress && opts.afterLatestUser && !opts.hasAnswerText;
}
