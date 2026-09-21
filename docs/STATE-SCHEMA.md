# Claus State Shapes for AI

This is not a database schema. This document describes conceptual AI-facing collaboration and runtime state for planning and review.

The shapes below are pseudo-schemas only. They are not tables, ORM models, migrations, API contracts, or implementation requirements.

## CollaborationContext

`CollaborationContext` represents the context available to a person or AI participant for a specific interaction.

Conceptual fields:

- `context_type`: personal_chat, personal_topic, or team_topic.
- `context_id`: stable identifier for the context.
- `thread_id`: active thread when the interaction is scoped below the Topic level.
- `participants`: people and shared AI participants visible in this context.
- `messages`: relevant conversation items.
- `files`: files explicitly available to the context.
- `knowledge_refs`: RAG/knowledge references available under the current permissions.
- `active_tasks`: task references started from this context.
- `artifacts`: persistent outputs associated with the context.
- `constraints`: permissions, tool limits, approvals, and other execution constraints.

Conceptual pseudo-schema:

```json
{
  "context_type": "personal_chat | personal_topic | team_topic",
  "context_id": "context identifier",
  "thread_id": "optional thread identifier",
  "participants": ["ParticipantRef"],
  "messages": ["MessageRef"],
  "files": ["FileRef"],
  "knowledge_refs": ["KnowledgeReference"],
  "active_tasks": ["AgentTaskRef"],
  "artifacts": ["ArtifactRef"],
  "constraints": ["permission or execution constraint"]
}
```

## AgentTaskState

`AgentTaskState` represents a long-running or tool-using AI task that is separate from the conversational message that requested it.

Conceptual fields:

- `task_id`: stable identifier.
- `context_ref`: personal or team context that owns the task.
- `requested_by`: user or AI participant that initiated the task.
- `status`: queued, running, waiting_for_approval, succeeded, failed, cancelled, or similar state.
- `plan`: current high-level task plan when one exists.
- `current_step`: current execution step.
- `tool_runs`: tool execution summaries.
- `outputs`: artifact/file/message references produced by the task.
- `approval`: current approval requirement or result.
- `error`: safe failure summary when applicable.

Conceptual pseudo-schema:

```json
{
  "task_id": "task identifier",
  "context_ref": "context identifier",
  "requested_by": "participant identifier",
  "status": "task status",
  "plan": ["task step"],
  "current_step": "step identifier",
  "tool_runs": ["ToolRun"],
  "outputs": ["output reference"],
  "approval": "approval state or null",
  "error": "safe error summary or null"
}
```

## ToolRun

`ToolRun` represents one traceable tool or runtime action.

Fields:

- `tool_name`: Browser, Playwright, Terminal, Workspace, retrieval, model tool, API tool, or another approved tool.
- `action_id`: stable action identifier.
- `params_summary`: safe summary without secrets or credentials.
- `started_at`: start timestamp.
- `finished_at`: finish timestamp.
- `status`: succeeded, failed, skipped, blocked, or waiting_for_approval.
- `output_refs`: artifact, file, message, log, or other persistent result references.

Conceptual pseudo-schema:

```json
{
  "tool_name": "tool name",
  "action_id": "action identifier",
  "params_summary": "safe parameter summary",
  "started_at": "start timestamp",
  "finished_at": "finish timestamp",
  "status": "tool status",
  "output_refs": ["persistent output reference"]
}
```

## KnowledgeReference

`KnowledgeReference` represents retrievable context and its scope. A shared file is not automatically a `KnowledgeReference`.

Fields:

- `source_type`: uploaded_file, internal_document, external_page, database, or another source type.
- `source_ref`: stable source identifier or location.
- `scope`: personal, topic, team, organization, or external.
- `owner_ref`: owner or managing context when applicable.
- `indexed_at`: indexing timestamp when indexed.
- `version_or_validity`: version, effective date, or validity information when applicable.
- `summary`: concise retrieval summary.

Conceptual pseudo-schema:

```json
{
  "source_type": "source type",
  "source_ref": "source reference",
  "scope": "personal | topic | team | organization | external",
  "owner_ref": "owner reference",
  "indexed_at": "indexing timestamp or null",
  "version_or_validity": "version or validity metadata",
  "summary": "retrieval summary"
}
```

## Artifact

`Artifact` represents a persistent result created, uploaded, or derived during collaboration.

Fields:

- `artifact_id`: stable identifier.
- `artifact_type`: document, image, video, chart, table, html, notebook_result, file, browser_snapshot, or similar type.
- `context_ref`: owning personal/team context.
- `source_task_id`: task that produced the artifact when applicable.
- `storage_ref`: persistent storage reference.
- `visibility`: personal or shared visibility scope.
- `created_at`: creation timestamp.

Conceptual pseudo-schema:

```json
{
  "artifact_id": "artifact identifier",
  "artifact_type": "artifact type",
  "context_ref": "context identifier",
  "source_task_id": "task identifier or null",
  "storage_ref": "storage reference",
  "visibility": "personal or shared scope",
  "created_at": "creation timestamp"
}
```

## BrowserSession

`BrowserSession` represents an interactive browser runtime attached to a task.

Fields:

- `session_id`: stable identifier.
- `task_id`: owning task.
- `status`: starting, ready, active, stopping, stopped, or failed.
- `controller`: user, AI, or none when explicit control handoff is implemented.
- `viewer_scope`: participants allowed to view the session.
- `started_at`: start timestamp.
- `ended_at`: end timestamp.

Browser runtime state is ephemeral by default. Persistent outputs must be returned through files, artifacts, task state, or messages.

## StageItem

`StageItem` represents an item currently presented in the shared viewing surface.

Fields:

- `item_id`: stable identifier.
- `context_ref`: owning Topic/Thread or personal context.
- `source_type`: artifact or live_browser.
- `source_ref`: artifact or browser session reference.
- `presenter_ref`: current presenter when applicable.
- `presentation_state`: page, slide, playback position, filter, or other synchronized presentation state.

`StageItem` is presentation state, not the canonical storage location for the underlying file or artifact.
