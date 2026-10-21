---
name: documentation
description: Draft and maintain clear technical documentation such as READMEs, API references, runbooks, architecture docs, and onboarding guides from supplied or authorized source material.
---

# Technical Documentation

Write for a specific reader and task. Treat technical claims as evidence-bound: do not invent behavior, configuration, permissions, examples, owners, or operational steps. Mark placeholders and facts requiring confirmation.

## Document types and essential content

### README
- What the project is and why it exists
- Quick start with prerequisites and a path to first success
- Configuration, usage, and common troubleshooting
- Contribution and support guidance, if known

### API documentation
- Endpoints, methods, request/response schemas and examples
- Authentication, authorization, errors, limits, pagination, and versioning where applicable
- Examples that match verified contracts; label illustrative examples

### Runbook
- Purpose and when to use it
- Required access, prerequisites, risks, and escalation path
- Ordered steps, expected observations, decision points, and rollback/recovery
- State explicitly which actions require approval and authorized access

### Architecture document
- Context, goals, scope, and constraints
- Design and component/data-flow diagrams (ASCII or described)
- Interfaces, key decisions, alternatives, and trade-offs
- Risks, assumptions, and open questions

### Onboarding guide
- Environment prerequisites, setup, key systems, common tasks, and support contacts
- Include only verified steps; flag organization-specific details that need confirmation

## Principles

1. Write for the reader and their task; put the useful information first.
2. Prefer concrete examples, diagrams, commands, and screenshots only when supported by reliable source material and safe to use.
3. Keep material maintainable: link to authoritative documentation rather than duplicating it; avoid links the reader cannot access.
4. Distinguish verified facts from assumptions, examples, and missing details.
5. Date/version context where relevant and identify review triggers for volatile instructions.
6. Do not publish or modify shared documentation without explicit approval and authorization.

## Method

Confirm audience, purpose, scope, source of truth, desired format, and access constraints. Draft with a clear information hierarchy, check consistency against supplied evidence, then list unresolved questions and sections needing owner review. If a repository or knowledge base is not actually configured and authorized, request the source text or relevant excerpts rather than claiming inspection.