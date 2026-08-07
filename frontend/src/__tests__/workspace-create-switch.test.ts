import { describe, expect, it, vi, beforeEach } from "vitest";

const createMock = vi.fn();
const switchMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  },
  workspaces: {
    create: (...args: unknown[]) => createMock(...args),
    list: vi.fn(),
  },
  me: {
    switchWorkspace: (...args: unknown[]) => switchMock(...args),
  },
}));

describe("create + switch workspace flow", () => {
  beforeEach(() => {
    createMock.mockReset();
    switchMock.mockReset();
  });

  it("creates a workspace then switches into it", async () => {
    const ws = {
      id: "ws-1",
      organisation_id: "org-1",
      slug: "eng",
      display_name: "Engineering",
      description: null,
      is_active: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    createMock.mockResolvedValue(ws);
    switchMock.mockResolvedValue({
      access_token: "token",
      expires_in: 900,
      organisation_id: "org-1",
      workspace_id: "ws-1",
      workspace_role: "administrator",
    });

    const { workspaces, me } = await import("@/lib/api");
    const created = await workspaces.create({
      slug: "eng",
      display_name: "Engineering",
    });
    expect(created.id).toBe("ws-1");
    const switched = await me.switchWorkspace(created.id);
    expect(switched.workspace_role).toBe("administrator");
    expect(createMock).toHaveBeenCalledOnce();
    expect(switchMock).toHaveBeenCalledWith("ws-1");
  });
});
