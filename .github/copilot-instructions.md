# Claus Copilot Instructions

Follow [`docs/CONTEXT.md`](../docs/CONTEXT.md) first. It is the canonical source of truth for AI-facing context and instructions.

Guardrails:

- Preserve personal and shared team context boundaries.
- Do not automatically promote uploaded files to shared or organization-wide RAG knowledge.
- Keep conversation, background tasks, files/artifacts, and execution runtimes separate.
- Do not rely on the GIL for application correctness.
- Do not use root, sudo, or administrator privileges.
- Do not run destructive commands.
- Do not write or commit secrets, API keys, passwords, or tokens.
- Do not create DB migrations, implement ORM/schema changes, install packages, or change lockfiles without explicit approval.
