---
name: architecture
description: Create or evaluate an architecture decision record, compare technologies, review a design proposal, or choose a component approach from supplied requirements and constraints.
argument-hint: "<decision or system to design>"
---

# Architecture Decisions

Create an Architecture Decision Record (ADR) or evaluate a system design. For requirements gathering, scaling, and deeper component analysis, pair this workflow with `../system-design/SKILL.md`.

## Modes

- **Create an ADR:** define the decision, compare named alternatives, and record consequences.
- **Evaluate a design:** assess it against stated functional and non-functional requirements; identify strengths, risks, and unanswered questions.
- **Frame a system design:** outline options and constraints before selecting a direction.

## Method

1. **Establish context.** Record the problem, goals, constraints, current state as supplied, affected stakeholders, and decision deadline if known. Ask for missing decision-critical requirements.
2. **Separate facts and assumptions.** Identify sourced facts, hypotheses, and unknowns. Do not present estimates or existing-system properties as verified without evidence.
3. **Name viable options.** Include a meaningful alternative (including doing nothing where appropriate); describe each consistently.
4. **Compare trade-offs.** Consider complexity, cost, scalability, reliability, security, operational burden, team familiarity, time to market, and reversibility where relevant. Explain why dimensions matter here.
5. **Recommend explicitly.** State a recommendation with rationale and confidence; identify what evidence could change it.
6. **Record consequences.** Capture benefits, costs, risks, migration/implementation steps, and revisit triggers. Mark owners and due dates as TBD unless provided.

## ADR template

```markdown
# ADR-[number or TBD]: [Title]

**Status:** Proposed | Accepted | Deprecated | Superseded
**Date:** [date or TBD]
**Deciders:** [provided names/roles or TBD]

## Context
[Situation, goals, forces, constraints, and evidence sources]

## Decision
[Proposed or accepted decision, clearly labeled]

## Options considered
### Option A: [name]
| Dimension | Assessment | Evidence / assumption |
|---|---|---|
| Complexity | [assessment] | [basis] |
| Cost | [assessment] | [basis] |
| Scalability | [assessment] | [basis] |
| Team familiarity | [assessment] | [basis] |
**Pros:** …
**Cons:** …

### Option B: [name]
[Same dimensions and structure]

## Trade-off analysis
[Why the relevant trade-offs favor one choice]

## Consequences
- Easier: …
- Harder / risks: …
- Revisit if: …

## Open questions
- [Unverified requirements or assumptions]

## Action items
- [ ] [Step; owner/date only when known]
```

## Limits and optional sources

Use only supplied evidence or external material from a source that is actually configured and authorized. Prior ADRs, team standards, tickets, and cost data are not presumed available. If unavailable, ask the caller to provide them; do not create tickets or change architecture on their behalf. A recommendation is not an approval.