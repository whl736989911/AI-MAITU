---
name: system-design
description: Design or evaluate systems, services, APIs, data models, and component boundaries from explicit requirements and constraints.
---

# System Design

Produce a clear design grounded in supplied requirements. Ask focused questions when a missing requirement could change the design; otherwise state assumptions and their consequences.

## Framework

### 1. Requirements and constraints
- Functional requirements: capabilities, actors, critical flows, and boundaries.
- Non-functional requirements: scale, latency, availability, durability, security/privacy, cost, operability.
- Constraints: timeline, team size/expertise, existing stack, compatibility, regulatory or deployment limits.
- Separate measured facts, targets, estimates, and unknowns. Do not invent traffic or SLO values.

### 2. High-level design
- Show components and ownership boundaries with an ASCII or described diagram.
- Explain data flow and trust boundaries.
- Define key API/event contracts, storage choices, and consistency expectations.

### 3. Deep dives
Where relevant, detail data models, API design (REST/GraphQL/gRPC), caching, queues/events, error handling, retries, idempotency, and migration strategy. Identify failure modes and backpressure.

### 4. Scale and reliability
Estimate load only from supplied inputs and show calculations/assumptions. Consider horizontal/vertical scaling, bottlenecks, redundancy, failover, recovery objectives, monitoring, and alerting. Distinguish proposed observability from existing instrumentation.

### 5. Trade-offs and decision
Compare complexity, cost, team familiarity, delivery time, security, reliability, and maintainability. Recommend an approach, explain alternatives and consequences, and identify what should be revisited as the system grows.

## Output

```markdown
# [System] Design
## Goals and non-goals
## Requirements and constraints
## Assumptions and open questions
## Proposed design
[Diagram and component responsibilities]
## Interfaces and data model
## Failure handling, security, and operations
## Capacity / scale analysis
[Inputs, calculations, uncertainties]
## Alternatives and trade-offs
## Risks, rollout / migration, and revisit triggers
```

External architecture, traffic, cost, or monitoring data is not presumed accessible. Use only supplied or actually available and authorized sources; otherwise label assumptions or request material. This is design guidance, not approval to implement or deploy.