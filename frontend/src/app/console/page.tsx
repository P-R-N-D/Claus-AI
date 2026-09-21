import Link from "next/link";

export default function ConsolePage() {
  return (
    <main className="min-h-screen bg-slate-100 px-6 py-10 text-slate-900 dark:bg-slate-950 dark:text-slate-100 sm:px-8 lg:px-10">
      <section className="mx-auto max-w-5xl rounded-[2rem] border border-slate-200 bg-white p-8 shadow-xl dark:border-slate-800 dark:bg-slate-900 sm:p-10">
        <p className="text-sm font-semibold uppercase tracking-[0.3em] text-violet-600 dark:text-violet-400">Claus Console</p>
        <h1 className="mt-4 text-4xl font-bold tracking-tight">Operations workspace</h1>
        <p className="mt-4 max-w-2xl leading-7 text-slate-600 dark:text-slate-300">
          This separate product surface is reserved for future team, task, agent, indexing, usage, failure, runtime, and policy operations.
        </p>
        <Link className="mt-8 inline-flex rounded-full bg-slate-950 px-5 py-2 text-sm font-semibold text-white dark:bg-white dark:text-slate-950" href="/">
          Return to user workspace
        </Link>
      </section>
    </main>
  );
}
