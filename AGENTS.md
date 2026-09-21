# Claus Codex Entry Point

Read [`docs/CONTEXT.md`](docs/CONTEXT.md) first. It is the canonical source of truth for AI-facing context and instructions.

Guardrails:

- Preserve the boundary between personal context and shared team context.
- Do not expose private AI conversations, personal files, or personal working context to team topics unless the user explicitly shares them.
- Treat file sharing and RAG/knowledge indexing as separate operations.
- Keep conversation, background tasks, files/artifacts, and execution runtimes as separate concerns.
- Do not rely on the GIL for application correctness; shared mutable state requires explicit synchronization or external state management.
- Do not use root, sudo, or administrator privileges.
- Do not run destructive commands.
- Do not write or commit secrets, API keys, passwords, or tokens.
- Do not create DB migrations, implement ORM/schema changes, install packages, or change lockfiles without explicit approval.
