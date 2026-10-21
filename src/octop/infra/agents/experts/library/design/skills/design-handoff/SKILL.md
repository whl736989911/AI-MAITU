---
name: design-handoff
description: Prepare an engineering handoff specification from supplied design screenshots, descriptions, files, and constraints. Cover layout, known tokens and measurements, states, interactions, responsive behavior, edge cases, motion, accessibility, and unresolved decisions.
argument-hint: "<screenshot, supplied file, or design description>"
---

# Design handoff

Turn supplied design evidence into a clear developer-facing specification. Make explicit what is known, what is inferred, and what remains to be decided. A screenshot or description can support a useful handoff; it does not reveal exact hidden dimensions, design tokens, responsive breakpoints, assets, or behavior.

## Inputs and access

Use supplied screenshots, design exports, specifications, and product constraints. Ask for implementation stack or platform only when it changes the handoff. A Figma/design URL is not evidence of connector access. Direct design-tool or project-tracker use is optional and only when actually configured and authorized for the caller and resource. Otherwise work from supplied material; do not claim export, ticket creation, or linking.

## What to specify

### Visual specifications
- Layout structure and alignment; exact spacing, sizes, and colors only when present in evidence.
- Token references only when supplied; otherwise identify values to confirm rather than creating token names.
- Responsive breakpoints and transformations only when documented or shown. Mark uncovered viewport behavior as open.
- Component variants, states, and content hierarchy.

### Interactions
- Click/tap and keyboard behavior; hover/focus/active/disabled/loading/error/empty states as evidenced or explicitly proposed.
- Transitions, animations, duration/easing, and gesture behavior only when specified; mark proposals as proposals.

### Content and edge cases
- Character limits, truncation, localization, empty/loading/error states.
- Minimum/maximum content, long translated strings, missing data, slow/failing connections, and permission states where relevant.

### Accessibility
- Focus order, labels/names/roles, keyboard operation, status announcements, contrast/target-size requirements, and reduced-motion considerations.
- Clearly separate design recommendations from implementation verification; cite precise WCAG criteria for criterion-level findings.

## Principles

1. Do not assume: label missing values `Unknown / to confirm`.
2. Prefer actual supplied tokens to raw values; never invent token labels or claim values came from a design tool.
3. Cover meaningful states and boundary conditions, clearly labeling unshown states as required decisions or recommendations.
4. Explain intent where it supports implementation decisions, without inventing user research rationale.
5. Avoid translating proposals into approved requirements; make status explicit.

## Output

```markdown
## Handoff Spec: [Feature/Screen]
**Source material:** [files/screenshots/descriptions] | **Platform/stack:** [known or to confirm] | **Status:** [draft/approved only if supplied]

### Overview
[Purpose and known user context; identify assumptions]

### Layout and responsive behavior
[Structure and visible behavior; table breakpoints only when supplied, otherwise list unknowns]

### Measurements and design tokens
| Element | Supplied measurement/token | Usage | Source or status |
|---|---|---|---|

### Components
| Component | Variant | Properties | Source/evidence | Open questions |
|---|---|---|---|---|

### States and interactions
| Element | State/trigger | Behavior | Source or proposed | Unresolved detail |
|---|---|---|---|---|

### Content and edge cases
- **Empty/loading/error:** [known behavior or decision needed]
- **Long/localized content:** [known limits or test needed]
- **Missing data/failure:** [known behavior or decision needed]

### Motion
| Trigger | Behavior | Duration/easing | Source or status |
|---|---|---|---|

### Accessibility notes
[Evidence-grounded recommendations, cited criteria where applicable, and validation still needed]

### Decisions / unknowns
- [Question, owner if supplied, and impact]
```

Do not provide precise measurements, token values, breakpoints, or component props without source evidence. A generated handoff is not implementation or design approval.