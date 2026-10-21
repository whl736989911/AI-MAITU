---
name: update
description: Review user-provided tasks and materials, triage stale or blocked items, and propose confirmed changes without assuming external integrations or automatic synchronization.
argument-hint: "[review scope]"
---

# Update: Review Tasks and Context

This is an interactive review, not a background sync. Work only from material the user provides or sources they explicitly authorize and that are genuinely accessible. Do not claim to have scanned chat, email, documents, calendars, or trackers without an authorized configured connector and an actual successful read.

## 1. Confirm review scope and access

Ask which task list, time period, or sources to review when unclear. If the user expects an external source, check whether the relevant connector is actually available and authorized for this caller and scope. If not, state the missing connector plainly and ask the user to paste/upload the relevant tasks or notes.

Reviewing material does not authorize persisting it. Read or write the shared workspace `TASKS.md` only if the user explicitly requested task-file maintenance and confirmed the feature workspace is appropriate for those tasks. Do not access private material through a shared workspace or create shared personal-memory files.

## 2. Load authorized information

Use only the user-provided material and any task file/source whose access is explicitly permitted. If no usable source is available, ask for it rather than fabricating a review. Keep source attribution where available and separate direct facts from your interpretations.

## 3. Compare and triage

For each candidate task or task-list entry, classify:

| Observation | Suggested handling |
|---|---|
| Clearly new action item | Show as a candidate; ask before adding |
| Likely duplicate | Explain the possible match; ask if uncertain |
| Explicitly complete | Ask before marking done unless the user directly instructed that exact change |
| Past stated due date | Flag as overdue and ask whether to complete, reschedule, keep, or move |
| Waiting on another person | Preserve the dependency; ask if status or next follow-up is unclear |
| Long-running or context-free | Flag for review; do not infer it is stale |
| No longer relevant | Ask whether to keep, move to Someday, or remove; do not alter unilaterally |

Do not use an arbitrary age threshold as proof that work is stale. State the concrete basis for a concern (such as an explicit due date). Do not silently delete, close, re-date, or re-prioritize tasks.

## 4. Check context gaps

Identify unclear owners, dates, acronyms, project labels, dependencies, and commitments. Ask only targeted questions that affect correct action. Use context already supplied in the current conversation; do not make up people’s identities, relationships, terminology, or project status. Do not write personal details or preferences to shared files. MAITU caller-private memory may be used only when available and explicitly authorized; otherwise do not persist personal context.

## 5. Confirm and apply changes

Present a concise proposed diff: candidate additions, status changes, removals, and unresolved questions. Obtain the user’s explicit approval for each change. Before any write to `TASKS.md`, recheck that the user authorized the file and confirmed its contents are suitable for the shared feature workspace. If that condition fails, leave the file untouched and return the proposed review in chat.

## 6. Report what actually happened

Summarize observed sources, agreed changes, unresolved items, and whether an authorized file was actually updated. If a connector was missing or inaccessible, identify it and note that its contents were not reviewed. Never represent suggestions as changes or claim a sync that did not occur.

## Broader review

For a broader review, the user must provide the relevant messages, notes, or documents (or explicitly authorize an available connector and scope). Extract likely action items and context as candidates, grouped by source and confidence. Explain uncertainty and ask which items to add. Do not infer tasks merely because a meeting occurred or a topic was mentioned.
