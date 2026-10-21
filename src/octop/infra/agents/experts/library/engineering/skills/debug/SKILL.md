---
name: debug
description: Systematically reproduce, isolate, diagnose, and plan a fix for an error or unexpected behavior using supplied evidence.
argument-hint: "<error or problem description>"
---

# Structured Debugging

Find causes systematically instead of guessing or suppressing symptoms.

## Workflow

### 1. Reproduce
- Clarify expected versus actual behavior, exact error text, environment, affected scope, and reproduction steps.
- Separate confirmed observations from reports or assumptions.
- If the issue is intermittent, record frequency, conditions, and what would count as a reproducible signal.

### 2. Isolate
- Narrow the component, service, code path, input, or time window.
- Review recent changes (deploys, configuration, dependencies) only when supplied or accessible through a configured, authorized source.
- Compare working and failing cases; inspect exact logs and stack traces supplied.

### 3. Diagnose
- Form competing hypotheses and rank them by evidence and discriminating test.
- Trace the relevant code/data flow if source is supplied and accessible.
- Propose low-risk tests that distinguish hypotheses. Do not claim a test was run unless it was.
- Identify root cause only when evidence supports it; otherwise report leading hypothesis and confidence as provisional.

### 4. Fix and prevent
- Propose the smallest root-cause fix, explain side effects and edge cases, and suggest regression tests.
- Keep mitigation separate from permanent correction. Avoid hiding exceptions, loosening validation, or special-casing symptoms unless evidence and user request justify it.
- Require explicit approval and an authorized mechanism before changing systems or files.

## Useful input

Exact error/stack trace; reproduction steps; expected and observed outputs; environment/version; recent relevant changes; logs or screenshots; affected users/scope; relevant code/config/test evidence. Ask for only missing decision-critical material.

## Output

```markdown
## Debug Report: [issue]

### Observations
- Expected: …
- Actual: …
- Reproduction / scope: …
- Evidence supplied: …

### Hypotheses and diagnosis
| Hypothesis | Evidence for/against | Discriminating check | Status |
|---|---|---|---|

### Root cause
[Confirmed explanation, or explicitly provisional/unknown]

### Proposed fix
[Change and rationale; side effects / risks]

### Regression prevention
- [Test or guard]

### Limitations
[Missing logs, source, environment, or checks]
```

External monitoring, source control, and tracking systems are not presumed accessible. If a configured, authorized tool is unavailable or not authorized, ask for relevant evidence. Never invent logs, commits, metrics, ticket state, or execution outcomes.