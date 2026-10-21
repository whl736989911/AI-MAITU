---
name: start
description: Help the user bootstrap a useful task overview from their stated goals and materials, while checking authorization and shared-workspace privacy before any persistent task-file write.
---

# Start: Establish a Working Task Overview

Initialize through conversation and user-provided material. There is no assumed connector, dashboard, personal-memory file, or automatic setup. Do not create `TASKS.md` unless the user explicitly authorizes it and confirms the shared feature workspace is appropriate for its contents.

## 1. Establish scope and permissions

Ask what the user wants to organize (current priorities, projects, a specific list, or provided notes). Before reading or writing an existing workspace task file, explain that this workspace is shared for the feature and ask whether it is appropriate for the tasks. Obtain explicit permission to use `TASKS.md`; permission to summarize tasks is not permission to store them.

If the user wants personal or sensitive tasks kept private, do not put them in the shared workspace. Keep the overview in the current conversation, or use only MAITU caller-private memory when that capability is available and explicitly authorized. Never create `USER.md`, `MEMORY.md`, or a replacement personal-memory directory.

## 2. Gather a source

Ask the user to provide their task list or relevant notes by pasting or uploading them. A connector may be used only when it is actually configured, authorized for this caller, and explicitly approved for this request. If no connector is available, say which source cannot be accessed and ask the user to provide its content; do not imply a scan or sync occurred.

## 3. Parse and clarify

From provided material, extract candidate actions, explicit owners, deadlines, dependencies, and source context. Separate actual commitments from ideas, discussion, or suggestions. Ask about ambiguous names, shorthand, project references, and dates only when needed to avoid a mistaken interpretation. Mark unknowns rather than guessing.

Present a concise draft overview grouped by a useful status (Active, Waiting On, Someday, Done) and highlight priorities or missing details. All extracted actions remain suggestions until the user confirms them.

## 4. Persist only with confirmation

If and only if the user explicitly authorized `TASKS.md` and confirmed that these tasks may be stored in the shared feature workspace:

1. Show the exact candidate entries and ask for confirmation before writing.
2. Check that each entry is appropriate for shared visibility and contains no private or sensitive details.
3. If the file does not exist, propose this simple structure and obtain confirmation to create it:

```markdown
# Tasks

## Active

## Waiting On

## Someday

## Done
```

4. Write only the confirmed entries. If authorization, privacy suitability, or source clarity is missing, do not write; continue with a conversation-only overview.

## 5. Report accurately

State what material was reviewed, what remains uncertain, and whether a user-confirmed shared task file was actually created or updated. Do not promise background maintenance, connector sync, or a generated dashboard. For future context, use MAITU caller-private memory only if it is available and explicitly permitted; otherwise request relevant context again when needed.
