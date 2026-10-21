---
name: design-critique
description: Give structured, constructive design feedback grounded in a supplied screenshot, description, prototype notes, or design files. Use for review requests at exploration, refinement, or final polish.
argument-hint: "<screenshot, supplied file, or design description>"
---

# Design critique

Review design evidence across usability, visual hierarchy, consistency, and accessibility. Give actionable alternatives and acknowledge what works; distinguish visual observations from assumptions about behavior or implementation.

## Inputs

- **Design**: screenshot, supplied image/file, or sufficiently detailed description.
- **Context**: purpose, user/audience, platform, and lifecycle stage when known.
- **Focus** (optional): a flow, viewport, or concern.

A URL or Figma reference is not proof of access. Work from supplied materials unless a design-tool connector is actually configured and authorized for the caller and resource. Do not promise direct inspection or infer absent layers, tokens, feedback, or analytics. Ask only for missing context that would change the critique.

## Critique framework

### 1. First impression
- What attracts attention first, and is it aligned with the likely primary goal?
- Is the purpose legible quickly from the available evidence?
- What emotional tone does the visual treatment suggest? Mark this as interpretation, not user-tested reaction.

### 2. Usability
- Can the apparent user goal be completed from the represented flow?
- Is navigation understandable and are interactive affordances distinguishable?
- Are unnecessary steps, ambiguity, or missing states visible? Do not assume unseen interaction behavior.

### 3. Visual hierarchy
- Is there a coherent reading order and emphasis?
- Are spacing, grouping, typography, and contrast supporting the content hierarchy?
- Is whitespace helping separation or making relationships unclear?

### 4. Consistency
- Are repeated elements visually and behaviorally consistent within the evidence?
- Compare against a supplied design system only. Without it, label deviations as possible inconsistency, not violations of an unseen system.

### 5. Accessibility
- Note observable readability, hierarchy, focus representation, target size, and alternative-content questions.
- Cite exact WCAG criterion only where the evidence supports a criterion-level finding. A mockup cannot establish keyboard, screen-reader, or measured contrast behavior; use `accessibility-review` for a fuller bounded review.

## Feedback principles

- Be specific: identify the element and evidence, not a vague judgment.
- Explain user impact or design rationale; separate fact from interpretation.
- Offer a feasible alternative, not just a diagnosis.
- Include strengths as well as opportunities.
- Match depth to stage: explore alternatives for early concepts; focus on clarity and polish for later stages.

## Output

```markdown
## Design Critique: [Name or supplied description]
**Evidence reviewed:** [source] | **Context/stage:** [known context or unknown] | **Limitations:** [what cannot be established]

### Overall impression
[1–2 sentences; distinguish observation and interpretation]

### Usability
| Finding and evidence | User impact | Priority | Recommendation |
|---|---|---|---|

### Visual hierarchy
- **First attention:** [element and whether it supports the goal]
- **Reading flow:** [visible sequence]
- **Emphasis/spacing/type:** [what works and opportunity]

### Consistency
| Element/pattern | Evidence and concern | Recommendation |
|---|---|---|

### Accessibility observations
| Observation | Criterion (if evidenced) | Limitation / next check |
|---|---|---|

### What works well
- [Specific strength]

### Priority recommendations
1. **[Highest impact]** — [why and how]
2. **[Next]** — [why and how]
3. **[Next]** — [why and how]
```

If a connected feedback source is genuinely available and authorized, its data may supplement supplied evidence; identify the source and distinguish it from visual critique. Otherwise request user-provided feedback. Never imply that a design, tracker, or analytics connector was queried.