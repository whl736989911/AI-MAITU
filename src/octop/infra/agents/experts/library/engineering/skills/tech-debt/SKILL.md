---
name: tech-debt
description: Identify, categorize, and prioritize evidenced technical debt and maintenance risks, then prepare a phased remediation proposal.
---

# Technical Debt Management

Use evidence from supplied material or source that is actually configured and authorized. Do not present suspected debt as a verified defect or invent effort, impact, business value, or backlog items.

## Categories

| Type | Examples | Potential risk |
|---|---|---|
| Code | Duplication, poor abstractions, magic values | Bugs, slower changes |
| Architecture | Boundaries or storage poorly suited to requirements | Change friction, scaling constraints |
| Test | Missing integration coverage, flaky tests | Regressions, weak confidence |
| Dependency | Outdated or unmaintained dependencies | Security and compatibility risk |
| Documentation | Missing/outdated runbooks or READMEs | Onboarding and recovery friction |
| Infrastructure | Manual processes, absent monitoring or IaC | Operational risk, slower recovery |

## Prioritization framework

For each item score from 1–5, documenting evidence and rationale:
- **Impact:** demonstrated team/product/operational drag.
- **Risk:** likely consequence of leaving it unaddressed.
- **Effort:** estimated remediation effort, inverted so low effort scores higher.

A simple triage score is **(Impact + Risk) × (6 − Effort)**. This is a prioritization aid, not an objective measurement; leave scores unknown where evidence is inadequate and do not rank false precision over judgment. Consider urgency, dependencies, reversibility, and opportunity cost separately.

## Deliverable

| Priority | Debt item and category | Evidence / confidence | Impact and risk | Effort / assumptions | Recommended next step |
|---|---|---|---|---|---|

Follow with rationale, dependencies, and a phased remediation proposal that can fit alongside feature work. Distinguish confirmed items from hypotheses, state what further evidence would validate them, and identify owners only when supplied. Do not create tickets, alter code, or claim repository audit completeness without explicit approval and authorized access.