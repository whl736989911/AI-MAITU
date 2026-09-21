import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PwaInstallPrompt from "./index";

describe("PwaInstallPrompt", () => {
  it("offers install from the chat float in a regular browser", () => {
    render(<PwaInstallPrompt appearance="chatFloat" />);
    expect(screen.getByLabelText("安装应用")).toBeInTheDocument();
  });
});
