---
name: testing-strategy
description: Design a risk-based test strategy and concrete test plan balancing coverage, feedback speed, and maintenance cost.
---

# Testing Strategy

Design tests around risk and system boundaries. This workflow proposes a plan; it does not run tests or claim existing coverage without evidence.

## Testing pyramid

```text
        / E2E \          Few, slower, high confidence across user flows
       / Integration \    Some, exercise component boundaries
      /   Unit tests   \  Many, fast and focused
```

Use the pyramid as a heuristic, not a quota. Add contract, property-based, performance, security, accessibility, or resilience tests where the risk warrants them.

## By component type

- **API endpoints:** unit tests for business logic, integration tests for HTTP/auth/data behavior, contract tests for consumers.
- **Data pipelines:** input validation, transformation correctness, idempotency, ordering, failure/replay behavior.
- **Frontend:** component and interaction tests, accessibility, visual regression where useful, representative end-to-end flows.
- **Infrastructure:** configuration validation, smoke tests, load/performance tests, resilience/chaos tests where safe and authorized.

## Coverage priorities

Prioritize business-critical paths, error handling, edge cases, security boundaries, data integrity, concurrency, migrations, and backward compatibility. Usually skip trivial getters/setters, framework internals, and one-off scripts unless risk justifies them. Test assertions should verify meaningful outcomes, not implementation details.

## Method

1. Establish behavior, supported environments, architecture boundaries, risks, and current test evidence.
2. Map requirements and failure modes to test cases and layers.
3. Identify test data, fixtures, isolation, determinism, and dependencies.
4. Order checks by feedback speed and release risk; define when a failure blocks release only from supplied policy.
5. Identify coverage gaps, flaky/expensive tests, and maintenance trade-offs.

## Output

```markdown
## Test Plan: [scope]
### Risks and assumptions
### Test matrix
| Behavior / risk | Test level | Scenario and expected result | Data / setup | Priority |
|---|---|---|---|---|
### Regression and compatibility checks
### Coverage gaps and non-goals
### Execution order / release criteria
[Only criteria supported by supplied policy]
```

Do not invent coverage targets or say tests passed. Use authorized test/CI sources only if actually available; otherwise request reports or code and label the plan as proposed.