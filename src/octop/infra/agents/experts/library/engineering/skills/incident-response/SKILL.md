---
name: incident-response
description: Support incident triage, factual status updates, safe mitigation planning, timelines, and blameless postmortems using supplied evidence.
argument-hint: "<incident description or alert>"
---

# Incident Response

Support the incident lead with structured analysis and communications. Stabilization comes before postmortem. This workflow does not page people, open channels, change production, or otherwise mitigate an incident itself.

## Phases

### 1. Triage
- Establish observed symptoms, start time/time zone, affected service/users, scope, severity criteria, and known dependencies.
- Assign incident commander, communications lead, and responders only from supplied/confirmed names; otherwise record roles as unassigned.
- Severity is organization-specific. Use the supplied policy; if absent, label any SEV1–4 suggestion provisional and state the criteria/assumptions.

Example provisional scale only when no local policy is supplied:
| Level | Illustrative impact | Response |
|---|---|---|
| SEV1 | Critical service unavailable or broad severe impact | Immediate coordinated response |
| SEV2 | Major capability degraded for many users | Urgent response |
| SEV3 | Limited or partial impact | Timely response |
| SEV4 | Low-impact issue or cosmetic defect | Normal prioritization |

### 2. Communicate
Prepare factual internal/customer updates as appropriate. Include what is happening, known impact, what has been done, next step, next update time, and uncertainty. Draft only; posting requires explicit approval and an authorized tool.

### 3. Mitigate
Prioritize reversible, risk-aware options. For each proposed action state expected benefit, risk, prerequisites, validation signal, and rollback. Record a timeline from evidence. An authorized incident lead must approve consequential action; mitigation may be performed only through an actually configured, authorized real tool. Never assert action or resolution not observed.

### 4. Postmortem (after stability)
Create a blameless record: impact, duration, sourced timeline, contributing conditions, detection/response, root cause when evidence supports it, what went well/poorly, and owned follow-ups. Use 5 Whys as an aid, not a forced conclusion. Mark unknowns; do not assign owners or dates without evidence.

## Status update template

```markdown
## Incident Update: [title]
**Severity:** [policy level / provisional / unknown]
**Status:** Investigating | Identified | Monitoring | Resolved (evidence required)
**Impact:** [known impact; quantify only from evidence]
**Last updated:** [timestamp and timezone]

### Current status
[Known facts and uncertainty]
### Actions taken
[Only verified actions]
### Next steps
[Proposed/approved actions, responsible role if known, ETA only if supplied]
### Next update
[Time or TBD]
### Timeline
| Time (zone) | Event | Evidence |
|---|---|---|
```

## Postmortem template

```markdown
## Postmortem: [title]
**Date:** [date] | **Duration:** [verified interval or unknown] | **Severity:** [level]
**Authors:** [provided names or TBD] | **Status:** Draft

### Summary
### Impact
### Timeline (time zone and source for each entry)
### Root cause and contributing factors (confirmed vs. open)
### Detection and response
### What went well / what could improve
### Follow-up actions
| Action | Owner | Priority | Due date | Status |
|---|---|---|---|---|
### Open questions
```

Keep all updates factual, timely, and blameless. Monitoring, incident-management, and chat tools are optional only if actually configured and caller-authorized. Otherwise ask for alert details, logs, and policy; explicitly state the access limitation. No automatic paging, channel creation, status posting, or mitigation.