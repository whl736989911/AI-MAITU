import type { KeyboardEvent as ReactKeyboardEvent } from "react";

/** True while an IME composition is active or committing its final key. */
export function isImeComposing(
  event: Pick<ReactKeyboardEvent, "nativeEvent" | "keyCode">,
): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229;
}
