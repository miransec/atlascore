import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    user: {
      user_id: "u1",
      email: "a@b.co",
      full_name: "Ada Lovelace",
      organisation_id: "o1",
      organisation_slug: "acme",
      org_role: "owner",
      workspace_id: "w1",
      is_platform_admin: false,
    },
    loading: false,
    setUser: vi.fn(),
    signOut: vi.fn(),
    refresh: vi.fn(),
    switchWorkspace: vi.fn(),
  }),
}));

const answerMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    knowledge: {
      ...actual.knowledge,
      answer: (...args: unknown[]) => answerMock(...args),
    },
  };
});

vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({
    push: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
  }),
}));

import AskAiPage from "@/app/dashboard/answer/page";

describe("Ask AI page states", () => {
  afterEach(() => cleanup());
  beforeEach(() => answerMock.mockReset());

  it("shows empty composer state", () => {
    render(<AskAiPage />);
    expect(
      screen.getByRole("heading", { name: /Ask AtlasCore about your workspace knowledge/i }),
    ).toBeInTheDocument();
  });

  it("renders no-evidence abstention with upload CTA", async () => {
    answerMock.mockResolvedValue({
      status: "abstain_no_evidence",
      answer_text: "",
      citations: [],
      evidence_band: "none",
      provider: "deterministic-test",
      model: "deterministic-test-v1",
      limitations: [],
      suspicious_count: 0,
    });
    const user = userEvent.setup();
    render(<AskAiPage />);
    const box = screen.getByRole("textbox");
    await user.type(box, "What is the refund policy?");
    await user.click(screen.getByRole("button", { name: /^Ask AtlasCore$/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/No relevant workspace evidence was found/i),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /Upload document/i })).toBeInTheDocument();
  });

  it("renders safe provider failure message", async () => {
    answerMock.mockResolvedValue({
      status: "provider_failure",
      answer_text: "",
      citations: [],
      evidence_band: "medium",
      provider: "openai",
      model: "gpt-4o",
      limitations: [],
      suspicious_count: 0,
    });
    const user = userEvent.setup();
    render(<AskAiPage />);
    await user.type(screen.getByRole("textbox"), "Summarise onboarding");
    await user.click(screen.getByRole("button", { name: /^Ask AtlasCore$/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/answer provider is temporarily unavailable/i),
      ).toBeInTheDocument(),
    );
  });
});
