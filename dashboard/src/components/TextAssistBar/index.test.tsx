import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const assist = vi.hoisted(() => vi.fn());
const errorMessage = vi.hoisted(() => vi.fn());

vi.mock("../../api/modules/textAssist", () => ({
  textAssistApi: { assist },
}));
vi.mock("../../utils/apiError", () => ({
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
}));
vi.mock("@/utils/antdMessage", () => ({
  message: { warning: vi.fn(), error: errorMessage },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: "en" },
  }),
}));

import { TextAssistBar } from "./index";

describe("TextAssistBar", () => {
  beforeEach(() => {
    assist.mockReset();
    errorMessage.mockReset();
  });
  it("previews model output and applies it only after explicit confirmation", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    assist.mockResolvedValue({ text: "AI suggestion" });
    const { rerender } = render(
      <TextAssistBar
        kind="team.description"
        getText={() => "Existing draft"}
        getName={() => "Team"}
        onApply={onApply}
      />,
    );

    await user.click(screen.getByRole("button", { name: "textAssist.action" }));
    expect(await screen.findByText("AI suggestion")).toBeInTheDocument();
    rerender(
      <TextAssistBar
        kind="team.description"
        getText={() => "Rerendered draft"}
        getName={() => "Renamed team"}
        onApply={onApply}
      />,
    );
    expect(screen.getByText("AI suggestion")).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "textAssist.apply" }));
    expect(onApply).toHaveBeenCalledWith("AI suggestion");
  });

  it("discards a completed preview when its entity scope changes", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    assist.mockResolvedValueOnce({ text: "First entity suggestion" });
    const props = {
      kind: "team.description" as const,
      getText: () => "",
      getName: () => "Team",
      onApply,
    };
    const { rerender } = render(<TextAssistBar {...props} scopeKey="team-1" />);

    await user.click(screen.getByRole("button", { name: "textAssist.action" }));
    expect(
      await screen.findByText("First entity suggestion"),
    ).toBeInTheDocument();

    rerender(<TextAssistBar {...props} scopeKey="team-2" />);
    expect(
      screen.queryByText("First entity suggestion"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "textAssist.apply" }),
    ).not.toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("does not revive an abandoned request after returning to the same entity", async () => {
    const user = userEvent.setup();
    let resolveOld!: (value: { text: string }) => void;
    assist.mockImplementationOnce(
      () => new Promise<{ text: string }>((resolve) => (resolveOld = resolve)),
    );
    const props = {
      kind: "team.description" as const,
      getText: () => "",
      getName: () => "Team",
      onApply: vi.fn(),
    };
    const { rerender } = render(<TextAssistBar {...props} scopeKey="team-1" />);

    await user.click(screen.getByRole("button", { name: "textAssist.action" }));
    rerender(<TextAssistBar {...props} scopeKey="team-2" />);
    rerender(<TextAssistBar {...props} scopeKey="team-1" />);
    await act(async () => {
      resolveOld({ text: "Abandoned edit suggestion" });
    });

    expect(
      screen.queryByText("Abandoned edit suggestion"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "textAssist.apply" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "textAssist.action" }),
    ).toBeEnabled();
  });

  it("keeps a new scope request active when the previous request settles", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    let resolveOld!: (value: { text: string }) => void;
    let resolveNew!: (value: { text: string }) => void;
    assist
      .mockImplementationOnce(
        () =>
          new Promise<{ text: string }>((resolve) => (resolveOld = resolve)),
      )
      .mockImplementationOnce(
        () =>
          new Promise<{ text: string }>((resolve) => (resolveNew = resolve)),
      );
    const props = {
      kind: "team.description" as const,
      getText: () => "",
      getName: () => "Team",
      onApply,
    };
    const { rerender } = render(<TextAssistBar {...props} scopeKey="team-1" />);

    await user.click(screen.getByRole("button", { name: "textAssist.action" }));
    rerender(<TextAssistBar {...props} scopeKey="team-2" />);
    const generateButton = screen.getByRole("button", {
      name: "textAssist.action",
    });
    expect(generateButton).toBeEnabled();
    await user.click(generateButton);
    expect(assist).toHaveBeenCalledTimes(2);
    expect(generateButton).toBeDisabled();

    await act(async () => {
      resolveOld({ text: "Old entity suggestion" });
    });
    expect(screen.queryByText("Old entity suggestion")).not.toBeInTheDocument();
    expect(generateButton).toBeDisabled();

    await act(async () => {
      resolveNew({ text: "New entity suggestion" });
    });
    expect(
      await screen.findByText("New entity suggestion"),
    ).toBeInTheDocument();
    expect(generateButton).toBeEnabled();
  });
  it("ignores an old scope error without ending the current request", async () => {
    const user = userEvent.setup();
    let rejectOld!: (reason?: unknown) => void;
    let resolveNew!: (value: { text: string }) => void;
    assist
      .mockImplementationOnce(
        () =>
          new Promise<{ text: string }>((_resolve, reject) => {
            rejectOld = reject;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise<{ text: string }>((resolve) => (resolveNew = resolve)),
      );
    const props = {
      kind: "team.description" as const,
      getText: () => "",
      getName: () => "Team",
      onApply: vi.fn(),
    };
    const { rerender } = render(<TextAssistBar {...props} scopeKey="team-1" />);

    await user.click(screen.getByRole("button", { name: "textAssist.action" }));
    rerender(<TextAssistBar {...props} scopeKey="team-2" />);
    const generateButton = screen.getByRole("button", {
      name: "textAssist.action",
    });
    await user.click(generateButton);
    expect(generateButton).toBeDisabled();

    await act(async () => {
      rejectOld(new Error("old request failed"));
    });
    expect(errorMessage).not.toHaveBeenCalled();
    expect(generateButton).toBeDisabled();

    await act(async () => {
      resolveNew({ text: "Current scope suggestion" });
    });
    expect(
      await screen.findByText("Current scope suggestion"),
    ).toBeInTheDocument();
  });

  it("ignores a failed request after the component unmounts", async () => {
    const user = userEvent.setup();
    let reject!: (reason?: unknown) => void;
    assist.mockImplementationOnce(
      () =>
        new Promise<{ text: string }>((_resolve, rejectRequest) => {
          reject = rejectRequest;
        }),
    );
    const { unmount } = render(
      <TextAssistBar
        kind="team.description"
        getText={() => ""}
        getName={() => "Team"}
        onApply={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "textAssist.action" }));
    unmount();
    await act(async () => {
      reject(new Error("late failure"));
    });
    expect(errorMessage).not.toHaveBeenCalled();
  });
});
