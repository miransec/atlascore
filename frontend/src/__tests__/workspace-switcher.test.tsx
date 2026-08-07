import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { WorkspaceSwitcher } from "@/components/shell/WorkspaceSwitcher";
import type { Workspace } from "@/lib/api";

const workspaces: Workspace[] = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    organisation_id: "22222222-2222-4222-8222-222222222222",
    slug: "eng",
    display_name: "Engineering",
    description: null,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "33333333-3333-4333-8333-333333333333",
    organisation_id: "22222222-2222-4222-8222-222222222222",
    slug: "ops",
    display_name: "Operations",
    description: null,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

describe("WorkspaceSwitcher", () => {
  afterEach(() => cleanup());
  beforeEach(() => vi.clearAllMocks());

  it("shows the current workspace name", () => {
    render(
      <WorkspaceSwitcher
        workspaces={workspaces}
        currentId={workspaces[0].id}
        onSwitch={vi.fn()}
        onCreate={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Switch workspace")).toHaveTextContent("Engineering");
  });

  it("calls onSwitch when selecting another workspace", () => {
    const onSwitch = vi.fn();
    render(
      <WorkspaceSwitcher
        workspaces={workspaces}
        currentId={workspaces[0].id}
        onSwitch={onSwitch}
        onCreate={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("Switch workspace"));
    fireEvent.click(screen.getByRole("menuitem", { name: /Operations/i }));
    expect(onSwitch).toHaveBeenCalledWith(workspaces[1].id);
  });

  it("calls onCreate from the create action", () => {
    const onCreate = vi.fn();
    render(
      <WorkspaceSwitcher
        workspaces={workspaces}
        currentId={workspaces[0].id}
        onSwitch={vi.fn()}
        onCreate={onCreate}
      />,
    );
    fireEvent.click(screen.getByLabelText("Switch workspace"));
    fireEvent.click(screen.getByRole("menuitem", { name: /Create workspace/i }));
    expect(onCreate).toHaveBeenCalled();
  });
});
