import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PermissionCheckboxPicker, type PermissionCatalogItem } from "./index";

const CATALOG: PermissionCatalogItem[] = [
  { key: "experts", category: "settings", label: "Experts", can_grant: true },
  { key: "teams", category: "settings", label: "Teams", can_grant: true },
];

describe("PermissionCheckboxPicker team dependencies", () => {
  it("keeps Teams unselected by default and adds Experts when Teams is selected", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <PermissionCheckboxPicker catalog={CATALOG} onChange={onChange} />,
    );

    const teams = screen.getByRole("button", { name: /Teams/ });
    expect(teams).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(teams);
    expect(onChange).toHaveBeenLastCalledWith(["teams", "experts"]);

    rerender(
      <PermissionCheckboxPicker
        catalog={CATALOG}
        value={["teams", "experts"]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Experts/ }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("preserves the dependency during settings group select and clear", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <PermissionCheckboxPicker catalog={CATALOG} onChange={onChange} />,
    );
    fireEvent.click(
      screen.getByRole("checkbox", { name: "perms.groupSettings" }),
    );
    expect(onChange).toHaveBeenLastCalledWith(["experts", "teams"]);

    rerender(
      <PermissionCheckboxPicker
        catalog={CATALOG}
        value={["experts", "teams"]}
        onChange={onChange}
      />,
    );
    fireEvent.click(
      screen.getByRole("checkbox", { name: "perms.groupSettings" }),
    );
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("explains when a team grant is ineffective because Experts is denied", () => {
    render(
      <PermissionCheckboxPicker
        catalog={CATALOG}
        value={["teams", "experts"]}
        deniedKeys={new Set(["experts"])}
      />,
    );
    expect(screen.getByRole("button", { name: /Teams/ })).toHaveAttribute(
      "title",
      "perms.teamsExpertDeniedNote",
    );
  });
});
