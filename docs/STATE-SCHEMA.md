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
- `visibility`: `personal` or `shared`. A presentation flag, not one of the five permission scopes; the owning scope follows `context_ref`.
- `created_at`: creation timestamp.

Conceptual pseudo-schema:

```json
{
  "artifact_id": "artifact identifier",
  "artifact_type": "artifact type",
  "context_ref": "context identifier",
  "source_task_id": "task identifier or null",
  "storage_ref": "storage reference",
  "visibility": "personal or shared",
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

## PresentationPreferences

`PresentationPreferences` represents how content is presented to one person: UI language, time zone, and the language expected from AI output. It is a conceptual shape for the direction in [I18N.md](I18N.md). Nothing persists it today; no account preference model, API, or cookie handling exists.

Fields:

- `subject_ref`: the user the preferences belong to.
- `ui_locale_preference`: `system`, `en`, or `ko`. `system` means "follow the environment language".
- `resolved_ui_locale`: `en` or `ko`, the locale actually used for a request after the resolution order in I18N.md (account setting, explicit cookie, environment language, English fallback).
- `resolution_source`: `account`, `cookie`, `environment`, or `default`, recorded for diagnostics.
- `time_zone`: IANA time zone name used for date and time rendering; absent means the application default.
- `ai_output_language_preference`: the language a user wants AI conversation and generated results in, kept separate from `ui_locale_preference` and possibly different from it.

Conceptual pseudo-schema:

```json
{
  "subject_ref": "user identifier",
  "ui_locale_preference": "system | en | ko",
  "resolved_ui_locale": "en | ko",
  "resolution_source": "account | cookie | environment | default",
  "time_zone": "IANA time zone or null",
  "ai_output_language_preference": "language preference or null"
}
```

`PresentationPreferences` never carries permissions, identifiers, enum values, or error codes; those stay locale-independent. Administrator-level defaults, when designed, are a separate policy object, not a field here.

## InteractionContext

`InteractionContext` describes how one operation was invoked: by a person in the Human UI, by an agent through a WebMCP tool, by automation, by Browser Computer Use, or by a background Task. It exists for audit and diagnostics. It is never an input to authorization, which is decided server-side from the actor, the `CollaborationContext`, and the operation. See [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md). Nothing records it today.

Fields:

- `origin`: `human_ui`, `webmcp`, `automation`, `browser_computer_use`, or `background_task`.
- `actor_ref`: the authenticated user on whose behalf the operation runs.
- `ai_participant_ref`: the AI participant involved, when one is.
- `context_ref`: the `CollaborationContext` the operation runs in.
- `task_ref`: the `AgentTaskState` that issued the operation, when applicable.
- `operation`: the application operation name invoked.
- `request_id`: a unique identifier for this invocation, usable as an idempotency key for retries.
- `approval_ref`: the approval record the operation relied on, when one was required.
- `recorded_at`: timestamp.

Conceptual pseudo-schema:

```json
{
  "origin": "human_ui | webmcp | automation | browser_computer_use | background_task",
  "actor_ref": "user identifier",
  "ai_participant_ref": "AI participant identifier or null",
  "context_ref": "context identifier",
  "task_ref": "task identifier or null",
  "operation": "application operation name",
  "request_id": "unique invocation identifier",
  "approval_ref": "approval identifier or null",
  "recorded_at": "timestamp"
}
```

`InteractionContext` does not duplicate `CollaborationContext` (what the actor may see), `AgentTaskState` (what a Task is doing), or `ToolRun` (what a runtime tool did); it references them and adds only the origin of the invocation.
