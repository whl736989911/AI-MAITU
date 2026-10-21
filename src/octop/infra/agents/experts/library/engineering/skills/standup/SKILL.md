---
name: standup
description: Structure caller-supplied work notes into a concise daily standup covering yesterday, today, and blockers.
argument-hint: "[yesterday | today | blockers]"
---

# Standup Update

Turn supplied notes into a concise, action-oriented update. Do not infer work from silence or fabricate commits, PRs, tickets, completion, plans, or blockers.

## Method

1. Collect the relevant time period and notes (rough bullets are enough); clarify date/time zone if needed.
2. Separate completed work from ongoing work and future plans. Preserve ticket references only when supplied.
3. State blockers with impact and help needed only when known; mark unknown details rather than filling them in.
4. Keep the result concise and suitable for the requested audience/format.

## Output

```markdown
## Standup — [date or TBD]

### Yesterday
- [Completed item supported by supplied information]

### Today
- [Planned item supported by supplied information]

### Blockers
- [Blocker, impact, and requested help if known; otherwise None reported / Not provided]
```

A date does not establish what the caller did. Source control, project tracking, chat, and CI/CD are not presumed available; use them only when actually configured and authorized. Otherwise ask the caller to provide relevant activity. Do not claim to have gathered it automatically, post updates, or create tickets without explicit approval and an authorized mechanism.