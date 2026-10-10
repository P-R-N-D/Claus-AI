"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Swal from "sweetalert2";
import { agentApi, coreApi, type HealthResponse } from "@/lib/api";

type LoadState = "loading" | "ok" | "error";
type ServiceHealth = { state: LoadState; health: HealthResponse | null };

const initialHealth: ServiceHealth = { state: "loading", health: null };

export function HealthCard() {
  const [core, setCore] = useState<ServiceHealth>(initialHealth);
  const [agent, setAgent] = useState<ServiceHealth>(initialHealth);
  const latestCheck = useRef(0);

  const loadHealth = useCallback((showAlert: boolean) => {
    latestCheck.current += 1;
    const check = latestCheck.current;
    return Promise.allSettled([
      coreApi.get<HealthResponse>("health/"),
      agentApi.get<HealthResponse>("health/"),
    ]).then(async ([coreResult, agentResult]) => {
      // A newer check, or unmounting, supersedes this one; never let an older result overwrite it.
      if (check !== latestCheck.current) return;
      setCore(coreResult.status === "fulfilled" ? { state: "ok", health: coreResult.value.data } : { state: "error", health: null });
      setAgent(agentResult.status === "fulfilled" ? { state: "ok", health: agentResult.value.data } : { state: "error", health: null });

      const failed = [
        coreResult.status === "rejected" ? "Core" : null,
        agentResult.status === "rejected" ? "Agent" : null,
      ].filter((service): service is string => service !== null);
      if (showAlert && failed.length > 0) {
        await Swal.fire({
          title: `${failed.join(" and ")} connection failed`,
          text: `Could not reach ${failed.join(" and ")} health ${failed.length === 1 ? "endpoint" : "endpoints"}.`,
          icon: "error",
          confirmButtonText: "OK",
        });
      }
    });
  }, []);

  useEffect(() => {
    void loadHealth(false);
    return () => {
      latestCheck.current += 1;
    };
  }, [loadHealth]);

  const checking = core.state === "loading" || agent.state === "loading";
  const retryHealth = () => {
    if (checking) return;
    // Loading state changes only here, in the event handler, never inside loadHealth.
    setCore((current) => ({ ...current, state: "loading" }));
    setAgent((current) => ({ ...current, state: "loading" }));
    void loadHealth(true);
  };

  return (
    <section className="rounded-3xl border border-slate-200 bg-white/90 p-6 shadow-xl shadow-slate-200/60 dark:border-slate-800 dark:bg-slate-900/90 dark:shadow-black/20">
      <div>
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-sky-600 dark:text-sky-400">Backend connection status</p>
          <h2 className="mt-2 text-2xl font-semibold text-slate-950 dark:text-white">Core + Agent</h2>
        </div>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        {([['Core', core], ['Agent', agent]] as const).map(([name, service]) => (
          <article key={name} aria-label={`${name} health`} className="rounded-2xl border border-slate-200 p-4 dark:border-slate-700">
            <div className="flex items-center justify-between gap-3">
              <h3 className="font-semibold text-slate-950 dark:text-white">{name}</h3>
              <span className={`rounded-full px-3 py-1 text-sm font-medium ${service.state === "ok" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300" : service.state === "error" ? "bg-rose-100 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300" : "bg-amber-100 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300"}`}>
                {service.state === "ok" ? "Connected" : service.state === "error" ? "Disconnected" : "Checking"}
              </span>
            </div>
            <pre className="mt-4 overflow-x-auto rounded-xl bg-slate-950 p-4 text-sm text-slate-100">
              {service.health ? JSON.stringify(service.health, null, 2) : service.state === "error" ? `${name} health response unavailable.` : `Checking ${name} health endpoint...`}
            </pre>
          </article>
        ))}
      </div>

      <button
        type="button"
        onClick={retryHealth}
        disabled={checking}
        aria-busy={checking}
        className="mt-5 cursor-pointer rounded-full bg-slate-950 px-5 py-2 text-sm font-semibold text-white shadow-lg transition hover:-translate-y-0.5 hover:bg-slate-800 disabled:translate-y-0 disabled:cursor-not-allowed disabled:bg-slate-950 disabled:opacity-60 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200 dark:disabled:bg-white"
      >
        Retry backend check
      </button>
    </section>
  );
}
