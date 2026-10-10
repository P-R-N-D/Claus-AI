import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { coreGet, agentGet, swalFire } = vi.hoisted(() => ({
  coreGet: vi.fn(),
  agentGet: vi.fn(),
  swalFire: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  coreApi: { get: coreGet },
  agentApi: { get: agentGet },
}));
vi.mock("sweetalert2", () => ({ default: { fire: swalFire } }));

import { HealthCard } from "@/components/HealthCard";

const coreHealth = { status: "ok", service: "claus-core", backend: "django", api: "drf" };
const agentHealth = { status: "ok", service: "claus-agent", backend: "fastapi" };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const card = (name: "Core" | "Agent") => within(screen.getByRole("article", { name: `${name} health` }));
const retryButton = () => screen.getByRole("button", { name: "Retry backend check" });

async function renderSettled(ui = <HealthCard />) {
  render(ui);
  await card("Core").findByText(/^(Connected|Disconnected)$/);
  await card("Agent").findByText(/^(Connected|Disconnected)$/);
}

afterEach(() => {
  cleanup();
  coreGet.mockReset();
  agentGet.mockReset();
  swalFire.mockReset();
});

describe("HealthCard", () => {
  it("checks both health endpoints on mount and shows each response", async () => {
    coreGet.mockResolvedValue({ data: coreHealth });
    agentGet.mockResolvedValue({ data: agentHealth });

    render(<HealthCard />);

    expect(card("Core").getByText("Checking")).toBeTruthy();
    expect(card("Agent").getByText("Checking")).toBeTruthy();
    expect(retryButton().getAttribute("aria-busy")).toBe("true");

    expect(await card("Core").findByText("Connected")).toBeTruthy();
    expect(await card("Agent").findByText("Connected")).toBeTruthy();
    expect(card("Core").getByText(/"service": "claus-core"/).textContent).toBe(JSON.stringify(coreHealth, null, 2));
    expect(card("Agent").getByText(/"service": "claus-agent"/).textContent).toBe(JSON.stringify(agentHealth, null, 2));
    expect(coreGet).toHaveBeenCalledExactlyOnceWith("health/");
    expect(agentGet).toHaveBeenCalledExactlyOnceWith("health/");
    expect(retryButton().getAttribute("aria-busy")).toBe("false");
    expect(retryButton().getAttribute("aria-disabled")).toBe("false");
  });

  it("marks only the failed service as disconnected and never alerts on the initial check", async () => {
    coreGet.mockRejectedValue(new Error("Network Error"));
    agentGet.mockResolvedValue({ data: agentHealth });

    await renderSettled();

    expect(card("Core").getByText("Disconnected")).toBeTruthy();
    expect(card("Core").getByText("Core health response unavailable.")).toBeTruthy();
    expect(card("Agent").getByText("Connected")).toBeTruthy();
    expect(swalFire).not.toHaveBeenCalled();
  });

  it.each([
    { failing: ["Core", "Agent"], title: "Core and Agent connection failed", text: "Could not reach Core and Agent health endpoints." },
    { failing: ["Agent"], title: "Agent connection failed", text: "Could not reach Agent health endpoint." },
  ])("alerts once after a retry in which $failing fail", async ({ failing, title, text }) => {
    coreGet.mockResolvedValue({ data: coreHealth });
    agentGet.mockResolvedValue({ data: agentHealth });
    await renderSettled();

    for (const [name, get, data] of [["Core", coreGet, coreHealth], ["Agent", agentGet, agentHealth]] as const) {
      if (failing.includes(name)) get.mockRejectedValue(new Error("Network Error"));
      else get.mockResolvedValue({ data });
    }
    fireEvent.click(retryButton());

    expect(retryButton().getAttribute("aria-busy")).toBe("true");
    expect(card("Core").getByText("Checking")).toBeTruthy();
    expect(card("Agent").getByText("Checking")).toBeTruthy();

    for (const name of failing) expect(await card(name as "Core" | "Agent").findByText("Disconnected")).toBeTruthy();
    expect(swalFire).toHaveBeenCalledExactlyOnceWith({ title, text, icon: "error", confirmButtonText: "OK" });
    // Each card reflects only its own service: no stale JSON on a failed card, no failure on a healthy one.
    for (const [name, data] of [["Core", coreHealth], ["Agent", agentHealth]] as const) {
      if (failing.includes(name)) {
        expect(card(name).getByText(`${name} health response unavailable.`)).toBeTruthy();
        expect(card(name).queryByText(/"service"/)).toBeNull();
      } else {
        expect(card(name).getByText("Connected")).toBeTruthy();
        expect(card(name).getByText(/"service"/).textContent).toBe(JSON.stringify(data, null, 2));
      }
    }
  });

  it("does not alert when a retry succeeds", async () => {
    coreGet.mockResolvedValue({ data: coreHealth });
    agentGet.mockResolvedValue({ data: agentHealth });
    await renderSettled();

    fireEvent.click(retryButton());

    expect(await card("Core").findByText("Connected")).toBeTruthy();
    expect(await card("Agent").findByText("Connected")).toBeTruthy();
    expect(coreGet).toHaveBeenCalledTimes(2);
    expect(agentGet).toHaveBeenCalledTimes(2);
    expect(swalFire).not.toHaveBeenCalled();
  });

  it("ignores Retry while a check is in flight", async () => {
    const pendingCore = deferred<{ data: typeof coreHealth }>();
    coreGet.mockReturnValue(pendingCore.promise);
    agentGet.mockResolvedValue({ data: agentHealth });

    render(<HealthCard />);
    // Both cards wait for both requests (Promise.allSettled), so the held Core request keeps them checking.
    await act(async () => {});
    expect(card("Agent").getByText("Checking")).toBeTruthy();
    expect(retryButton().getAttribute("aria-disabled")).toBe("true");

    fireEvent.click(retryButton());

    expect(coreGet).toHaveBeenCalledTimes(1);
    expect(agentGet).toHaveBeenCalledTimes(1);

    await act(async () => pendingCore.resolve({ data: coreHealth }));
    expect(card("Core").getByText("Connected")).toBeTruthy();
    expect(card("Agent").getByText("Connected")).toBeTruthy();
    expect(retryButton().getAttribute("aria-disabled")).toBe("false");
    expect(swalFire).not.toHaveBeenCalled();
  });

  it("does not alert for a retry that fails after the card unmounts", async () => {
    coreGet.mockResolvedValue({ data: coreHealth });
    agentGet.mockResolvedValue({ data: agentHealth });
    await renderSettled();

    const pendingCore = deferred<never>();
    coreGet.mockReturnValue(pendingCore.promise);
    agentGet.mockRejectedValue(new Error("Network Error"));
    fireEvent.click(retryButton());
    cleanup();

    await act(async () => pendingCore.reject(new Error("Network Error")));

    expect(coreGet).toHaveBeenCalledTimes(2);
    expect(swalFire).not.toHaveBeenCalled();
  });

  it("keeps the newest result when an older check settles later", async () => {
    // StrictMode mounts, unmounts and remounts in development, so the first check is superseded.
    const first = deferred<{ data: object }>();
    const second = deferred<{ data: object }>();
    coreGet.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    agentGet.mockResolvedValue({ data: agentHealth });

    render(
      <StrictMode>
        <HealthCard />
      </StrictMode>,
    );
    expect(coreGet).toHaveBeenCalledTimes(2);

    await act(async () => second.resolve({ data: { ...coreHealth, service: "newest" } }));
    expect(card("Core").getByText(/"service": "newest"/)).toBeTruthy();

    await act(async () => first.resolve({ data: { ...coreHealth, service: "superseded" } }));
    expect(card("Core").getByText(/"service": "newest"/)).toBeTruthy();
    expect(card("Core").queryByText(/"service": "superseded"/)).toBeNull();
  });
});
