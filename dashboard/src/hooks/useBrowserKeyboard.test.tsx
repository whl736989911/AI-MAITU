import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useBrowserKeyboard } from "./useBrowserKeyboard";

function setup(enabled = true) {
  const sendEvent = vi.fn((_event: Record<string, unknown>) => true);
  function Harness() {
    const { inputRef, focusInput, ...events } = useBrowserKeyboard(
      enabled,
      sendEvent,
    );
    return (
      <>
        <canvas tabIndex={0} onFocus={focusInput} />
        <textarea aria-label="Remote input" ref={inputRef} {...events} />
        <input aria-label="Address" />
      </>
    );
  }
  const view = render(<Harness />);
  return {
    ...view,
    input: screen.getByLabelText("Remote input") as HTMLTextAreaElement,
    sendEvent,
  };
}

describe("remote browser keyboard", () => {
  it("preserves intermediate composition input and sends committed Unicode once", async () => {
    const { input, sendEvent } = setup();
    fireEvent.compositionStart(input);
    fireEvent.input(input, {
      target: { value: "nihao" },
      isComposing: true,
      inputType: "insertCompositionText",
    });
    expect(input.value).toBe("nihao");
    expect(sendEvent).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.input(input, {
        target: { value: "你好" },
        isComposing: false,
        inputType: "insertFromComposition",
      });
      fireEvent.compositionEnd(input, { data: "你好" });
      await Promise.resolve();
    });
    expect(sendEvent).toHaveBeenCalledExactlyOnceWith({
      type: "insertText",
      text: "你好",
    });
    expect(input.value).toBe("");
  });

  it("handles the final input after compositionend without dropping the next character", async () => {
    const { input, sendEvent } = setup();
    fireEvent.compositionStart(input);
    fireEvent.input(input, {
      target: { value: "ni" },
      isComposing: true,
      inputType: "insertCompositionText",
    });
    expect(input.value).toBe("ni");

    await act(async () => {
      input.value = "你好";
      fireEvent.compositionEnd(input, { data: "你好" });
      fireEvent.input(input, {
        target: { value: "你好" },
        isComposing: false,
        inputType: "insertFromComposition",
      });
      await Promise.resolve();
    });
    fireEvent.input(input, {
      target: { value: "a" },
      inputType: "insertText",
    });
    expect(sendEvent.mock.calls.map(([event]) => event)).toEqual([
      { type: "insertText", text: "你好" },
      { type: "insertText", text: "a" },
    ]);
  });

  it("sends native paste and the immediately following ordinary input", () => {
    const { input, sendEvent } = setup();
    fireEvent.input(input, {
      target: { value: "测试 browser\n🙂" },
      inputType: "insertFromPaste",
    });
    fireEvent.input(input, { target: { value: "z" }, inputType: "insertText" });
    expect(sendEvent.mock.calls.map(([event]) => event)).toEqual([
      { type: "insertText", text: "测试 browser\n🙂" },
      { type: "insertText", text: "z" },
    ]);
  });

  it("does not forward composition Enter, Dead, Process, or 229 keys", () => {
    const { input, sendEvent } = setup();
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.keyDown(input, { key: "n", keyCode: 229, isComposing: true });
    fireEvent.compositionEnd(input, { data: "" });
    fireEvent.keyDown(input, { key: "Dead" });
    fireEvent.keyDown(input, { key: "Process" });
    expect(sendEvent).not.toHaveBeenCalled();
  });

  it("forwards editing keys and platform-select-all modifiers", () => {
    const { input, sendEvent } = setup();
    for (const key of [
      "Backspace",
      "Delete",
      "ArrowLeft",
      "ArrowRight",
      "Tab",
      "Enter",
    ]) {
      fireEvent.keyDown(input, { key, code: key, keyCode: 13, shiftKey: true });
    }
    fireEvent.keyDown(input, {
      key: "a",
      code: "KeyA",
      keyCode: 65,
      metaKey: true,
    });
    expect(sendEvent.mock.calls.map(([event]) => event.key)).toEqual([
      "Backspace",
      "Delete",
      "ArrowLeft",
      "ArrowRight",
      "Tab",
      "Enter",
      "a",
    ]);
    expect(sendEvent.mock.calls[0][0]).toMatchObject({
      type: "keydown",
      key: "Backspace",
      code: "Backspace",
      shiftKey: true,
      modifiers: 8,
    });
    expect(sendEvent.mock.calls.at(-1)?.[0]).toMatchObject({
      type: "keydown",
      key: "a",
      code: "KeyA",
      keyCode: 65,
      metaKey: true,
      modifiers: 4,
    });
  });

  it("focuses the input and does not send in read-only mode", () => {
    const { container, input, sendEvent } = setup(false);
    act(() => container.querySelector("canvas")!.focus());
    expect(input).not.toHaveFocus();
    fireEvent.keyDown(input, { key: "Backspace" });
    fireEvent.input(input, { target: { value: "a" } });
    fireEvent.keyDown(screen.getByLabelText("Address"), { key: "Backspace" });
    expect(sendEvent).not.toHaveBeenCalled();
  });
});
