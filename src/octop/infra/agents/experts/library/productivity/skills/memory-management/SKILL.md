---
name: memory-management
description: Interpret workplace shorthand and project context from the current conversation or user-provided material, with strict separation between feature-shared files and caller-private memory.
user-invocable: false
---

# Memory Management: Context Without Unsafe Persistence

Workplace shorthand can help interpret tasks, but a plausible interpretation is not a fact. This skill adapts the upstream productivity knowledge-work approach while enforcing MAITU shared-workspace privacy boundaries.

## Sources and boundaries

- Use facts and meanings that the user states in the current conversation or explicitly supplies in source material. Preserve who said what and where when that provenance is available.
- Do not assume a person, acronym, project codename, or tool has a particular meaning because it resembles a familiar example. Ask the user when an unknown term changes the interpretation or action.
- All files in the feature workspace are shared feature content. Never create, read, or write `USER.md`, `MEMORY.md`, a `memory/` directory, personal profiles, or preference files there. Do not place caller-private information into `TASKS.md` or any other shared artifact.
- For cross-conversation personal context, use only a MAITU-provided caller-private memory capability when it is actually available and explicitly authorized. If absent or unauthorized, do not create an alternative persistent store; use current-session context only and ask the user to repeat needed details later.
- External sources such as chat, email, calendar, or document systems are optional and not presumed available. Read them only through an actually configured connector, with caller authorization and explicit approval for this use. Otherwise request the material from the user and state the missing access.

## Interpreting an unfamiliar task

For a phrase such as “send the QBR notes to Alex about Atlas,” separate what is explicit from what is unknown:

- Explicit: an action to send notes; the referenced labels are “QBR,” “Alex,” and “Atlas.”
- Unknown unless supplied: what QBR means, which Alex, what Atlas refers to, intended recipient/channel, and deadline.

Ask concise clarifying questions only for unknowns needed to perform the request. Do not expand the task with fabricated titles, roles, relationships, or project descriptions. You may retain a confirmed meaning in the current conversation; persistent retention must follow the private-memory rule above.

## Capture and recall

When the user asks to remember or explains a term, acknowledge its meaning and source. Do not silently persist it. If an authorized private memory feature is available, follow its own access and confirmation requirements. Otherwise state that you can use it in this conversation but cannot safely save it to the shared feature workspace.

When asked to recall a term, look only in available conversation context or authorized private memory. If it is not present, say you do not know and ask for clarification. Do not claim an all-time glossary, long-term recall, or a completed scan without evidence.

## Optional user-provided reference material

If the user explicitly supplies a glossary or project notes for the current task, use them as task input and maintain source attribution in the response. Do not copy them into a shared feature file unless the user separately authorizes the exact write and confirms the content is suitable for shared visibility. A request to analyze material is not permission to retain it.

## Reporting

Distinguish clearly between confirmed context, uncertain interpretation, and information unavailable due to missing source access. Do not promise automatic memory growth, connector synchronization, background scans, or cross-session recall.
