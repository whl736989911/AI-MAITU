---
name: ux-copy
description: Write or review interface microcopy including CTAs, errors, empty states, confirmations, tooltips, loading states, and onboarding. Ground language in supplied product context and constraints.
argument-hint: "<interface context or copy to review>"
---

# UX copy

Write or review concise interface text that helps a person understand and complete a task. Work from supplied context; do not assume brand voice, legal policy, product behavior, or character limits. External brand guidelines or design context are optional and available only through a connector actually configured and authorized for the caller and resource. Otherwise ask for the guideline or supplied screen context.

## Context to establish

- Screen/flow and the user's goal or current state.
- What actually happened and what the interface can do next.
- Desired voice/tone and established terminology, if supplied.
- Character limits, localization, platform conventions, legal constraints, and consequences.

Ask only for missing details that materially affect accuracy. If a proposed message depends on unknown product behavior, mark that dependency instead of asserting it.

## Principles

1. **Clear**: use familiar language; avoid ambiguity and unexplained jargon.
2. **Concise**: remove words that do not help, without omitting essential context.
3. **Consistent**: use the same term for the same concept, following supplied vocabulary.
4. **Useful**: give the user the next actionable step where possible.
5. **Human**: adapt tone to context without blame, forced cheer, or false reassurance.

## Copy patterns

- **CTAs**: begin with a specific action verb; name the outcome (“Save changes”) rather than a vague action (“Submit”).
- **Errors**: state what happened, explain why only when known, and give a safe recovery path. Do not attribute failure to a bank, network, or user without evidence.
- **Empty states**: explain what the area is, why it may be empty only if known, and how to get started.
- **Confirmations**: name the consequential action and scope; state irreversibility only if true. Label actions with what they do rather than “OK/Cancel.”
- **Tooltips**: add concise information not already obvious or present elsewhere.
- **Loading**: set expectations only when timing/status is known; avoid invented countdowns.
- **Onboarding**: introduce one useful concept at a time and allow users to proceed at an appropriate pace.

## Voice and tone

Adapt to the user-provided voice. Common direction: success can be positive without over-celebrating; errors empathetic and actionable; warnings direct; neutral states informative. These are defaults to tailor, not assumed brand policy.

## Output

```markdown
## UX Copy: [Context]
**Known constraints:** [voice, platform, limits, terminology] | **To confirm:** [unknown behavior or policy]

### Recommended copy
**[Element/state]**: [copy]

### Alternatives
| Option | Copy | Tone | Suitable when | Tradeoff/constraint |
|---|---|---|---|---|

### Rationale
[How the copy supports the user's task; state assumptions]

### Localization and accessibility notes
[Idioms, expansion, terminology, reading level, screen-reader/context needs]
```

If reviewing existing text, identify specific ambiguity, tone, consistency, or usability concern and offer revised copy. Never claim compliance with a brand guide that was not provided or inspected.