---
name: code-review
description: Review supplied code changes for security, performance, correctness, and maintainability; use when evaluating a diff, patch, or code excerpt.
argument-hint: "<diff or code excerpt>"
---

# Code Review

Review the change evidence provided. A PR URL or file path does not itself grant access; if the content cannot be read through an actually configured, authorized source, ask for the diff or relevant excerpts.

## Review dimensions

### Security
Check injection (SQL, command, template, XSS), authentication and authorization, secrets, unsafe deserialization, path traversal, SSRF, trust boundaries, sensitive data handling, and relevant dependency/configuration risks.

### Performance and resource use
Look for N+1 queries, avoidable allocation or copying, algorithmic complexity in hot paths, missing indexes, unbounded loops/queries, resource leaks, contention, and unnecessary work. Tie concerns to demonstrated code paths; do not claim production impact without measurements.

### Correctness and reliability
Examine empty/null/invalid inputs, boundaries and overflow, state transitions, race conditions, error propagation, retries/idempotency, transaction boundaries, compatibility, and concurrency assumptions.

### Maintainability
Assess naming, cohesion, duplication, abstraction fit, tests, and documentation for non-obvious behavior. Separate preference from defects.

## Method

1. Confirm change scope and intended behavior. Request missing diff/context if needed.
2. Read the supplied change, then relevant surrounding code/tests only when actually available and authorized.
3. Trace each potential finding to concrete evidence; test mentally against a specific failure or threat scenario.
4. Prioritize by severity and impact; avoid speculative noise and duplicate findings.
5. Give a concise rationale and actionable remediation. Report strengths and unanswered questions separately.
6. Never say CI passed, a PR was checked, or requirements were met unless the corresponding evidence was actually observed.

## Output

```markdown
## Code Review: [identified change or supplied excerpt]

### Summary
[Scope actually reviewed and overall assessment]

### Findings
| # | Severity | Location | Finding | Evidence / scenario | Recommendation |
|---|---|---|---|---|---|
| 1 | Critical / High / Medium / Low | [file:line or excerpt] | … | … | … |

### Questions / unverified areas
- [Context not supplied or not inspected]

### Positive observations
- [Specific strengths, if any]

### Verdict
[Approve / Request changes / Needs discussion — recommendation only, not approval authority]
```

No findings is not proof of correctness; scope the conclusion to material reviewed. External CI, ticket, standard, or repository information is optional only when configured and authorized; otherwise ask the user to supply it. Do not modify files, tickets, or PR state without explicit approval and an authorized mechanism.