---
name: task-management
description: Parse, summarize, prioritize, and—with explicit authorization and shared-workspace privacy approval—maintain the feature workspace TASKS.md task artifact.
user-invocable: false
---

# Task Management

Help the user capture commitments without confusing a useful suggestion with permission to persist it. Task organization can always be done in the current conversation from available information. The optional `TASKS.md` workspace artifact has a stricter authorization and privacy gate.

## Before reading or writing TASKS.md

`TASKS.md` is shared feature workspace content, not a caller-private notebook. Only access it when the user explicitly asks to manage their task file and confirms that the shared workspace is appropriate for the specific tasks. Before each write, show the exact proposed changes and get confirmation. Do not store private/personal tasks, sensitive information, credentials, or caller preferences. If any gate is unclear, stop file access and offer a conversation-only summary. Never create `USER.md`, `MEMORY.md`, or personal-memory files/directories.

## Format

When the user authorizes creating the file, propose the following structure; create it only after confirmation:

```markdown
# Tasks

## Active

## Waiting On

## Someday

## Done
```

Suggested entry format: `- [ ] **Task** — context; owner if known; due YYYY-MM-DD if explicit`. Use sub-bullets for necessary dependencies or provenance. Do not invent due dates, owners, or context. Mark completion using `- [x] ~~Task~~ (YYYY-MM-DD)` only after the user confirms it is done.

## Working with tasks

- **“What’s on my plate?”** Summarize Active and Waiting On; mention explicit overdue dates and urgent items, and distinguish blockers from routine waits.
- **“Add these tasks.”** Parse all requested items, preserve explicit context and dates, identify ambiguity, and show a proposed list. Add to an authorized, privacy-suitable `TASKS.md` only after confirmation; otherwise provide the list in chat.
- **“This is done.”** Locate the matching task and ask if identification or completion is uncertain. Change its status or move it to Done only with explicit confirmation; retain the completion date only when known.
- **“What am I waiting on?”** Summarize Waiting On and dates/context if recorded; do not claim elapsed duration without a known start date.
- **Meeting or message summaries.** Offer extracted action items as candidates. Do not auto-add them, even if they sound like commitments; confirm the exact entries with the user first.
- **Stale or overdue tasks.** Flag them for triage and ask whether to complete, reschedule, keep, move to Someday, or remove. Never delete, close, or move an item without the user’s explicit choice.

## Prioritization

Use stated deadlines, impact, dependencies, and explicit user priorities. Separate urgent work from important but unscheduled work. If priorities conflict or critical information is missing, explain the trade-off and ask rather than silently imposing a plan.

## External task sources

A project tracker, mail, calendar, or chat source is not assumed to be connected. Use a connector only if it is actually configured, authorized for the caller, and explicitly approved for the requested scope. When it is unavailable, state that you cannot access that source directly and ask the user to provide relevant material. Never say the task list was synced unless a permitted read/write actually occurred and was confirmed.
