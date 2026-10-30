import {
  useCallback,
  useRef,
  type CompositionEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";

export function useBrowserKeyboard(
  enabled: boolean,
  sendEvent: (event: Record<string, unknown>) => boolean,
) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);

  const flushText = useCallback(
    (input: HTMLTextAreaElement) => {
      if (!enabled || composing.current || !input.value) return;
      sendEvent({ type: "insertText", text: input.value });
      input.value = "";
    },
    [enabled, sendEvent],
  );

  const focusInput = useCallback(() => {
    if (enabled) inputRef.current?.focus({ preventScroll: true });
  }, [enabled]);

  const onInput = useCallback(
    (event: FormEvent<HTMLTextAreaElement>) => {
      if (!(event.nativeEvent as InputEvent).isComposing) {
        flushText(event.currentTarget);
      }
    },
    [flushText],
  );

  const onCompositionStart = useCallback(() => {
    composing.current = true;
  }, []);

  const onCompositionEnd = useCallback(
    (event: CompositionEvent<HTMLTextAreaElement>) => {
      composing.current = false;
      const input = event.currentTarget;
      // Engines may emit the final non-composing input immediately before or
      // after compositionend. Flush the shared textarea value once both events
      // have had a chance to update it; clearing it makes a later input inert.
      queueMicrotask(() => {
        if (inputRef.current === input) flushText(input);
      });
    },
    [flushText],
  );

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (!enabled) return;
      event.stopPropagation();
      if (
        composing.current ||
        event.nativeEvent.isComposing ||
        event.keyCode === 229 ||
        event.key === "Process" ||
        event.key === "Dead"
      ) {
        return;
      }
      // Printable text, including paste and Unicode input, is handled by the
      // textarea's input event rather than keyboard characters.
      if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) return;
      if (["Shift", "Control", "Alt", "Meta", "AltGraph"].includes(event.key)) {
        return;
      }
      event.preventDefault();
      flushText(event.currentTarget);
      const modifiers =
        (event.altKey ? 1 : 0) |
        (event.ctrlKey ? 2 : 0) |
        (event.metaKey ? 4 : 0) |
        (event.shiftKey ? 8 : 0);
      sendEvent({
        type: "keydown",
        key: event.key,
        code: event.code,
        keyCode: event.keyCode,
        altKey: event.altKey,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        shiftKey: event.shiftKey,
        repeat: event.repeat,
        modifiers,
      });
    },
    [enabled, flushText, sendEvent],
  );

  return {
    inputRef,
    focusInput,
    onInput,
    onKeyDown,
    onCompositionStart,
    onCompositionEnd,
  };
}
