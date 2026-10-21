---
name: accessibility-review
description: Review a supplied design, screenshot, page description, or implementation evidence for accessibility concerns using precise WCAG criteria. Use for accessibility audits, contrast, keyboard, touch target, or screen-reader review requests.
argument-hint: "<screenshot, supplied file, or description>"
---

# Accessibility review

Review the supplied design or implementation evidence for likely accessibility barriers. This is evidence-bounded guidance, not a claim of conformance or a substitute for testing with people and assistive technologies.

## Inputs and evidence limits

Use screenshots, design descriptions, code/spec excerpts, or test notes the caller supplies. Ask for platform, target standard, relevant states, or implementation evidence when it materially changes findings. A screenshot can support visual observations but cannot establish keyboard operation, semantic structure, accessible names, screen-reader announcements, or actual contrast ratios unless color values are known and measured. Never claim a test occurred if it did not.

External design or issue-tracker connectors are optional and only usable when actually configured and authorized for the caller and resource. Otherwise ask for exports, source material, or permissioned evidence; do not assume a URL is accessible or promise ticket creation.

## WCAG 2.1 AA quick-reference candidates

Verify the normative criterion's applicability and wording before making a definitive claim; cite criterion number and name precisely and note the standard version/level.

### Perceivable
- **1.1.1 Non-text Content (A)**: meaningful non-text content has an appropriate text alternative.
- **1.3.1 Info and Relationships (A)**: information, structure, and relationships can be programmatically determined or available in text.
- **1.4.3 Contrast (Minimum) (AA)**: text contrast is at least 4.5:1, or 3:1 for large-scale text, subject to criterion exceptions.
- **1.4.11 Non-text Contrast (AA)**: visual information needed to identify UI components/states and meaningful graphical objects has at least 3:1 contrast against adjacent colors, subject to exceptions.

### Operable
- **2.1.1 Keyboard (A)**: functionality is operable through a keyboard interface, except where the underlying function depends on the path of movement.
- **2.4.3 Focus Order (A)**: focus order preserves meaning and operability.
- **2.4.7 Focus Visible (AA)**: keyboard focus indicator is visible.
- **2.5.8 Target Size (Minimum) (AA, WCAG 2.2)**: targets are at least 24 by 24 CSS pixels or meet a listed exception. The 44 by 44 CSS-pixel criterion is **2.5.5 Target Size (Enhanced) (AAA, WCAG 2.1)**, not AA; do not mislabel it.

### Understandable
- **3.2.1 On Focus (A)**: receiving focus does not initiate an unexpected context change.
- **3.3.1 Error Identification (A)**: detected input errors are identified and described in text.
- **3.3.2 Labels or Instructions (A)**: labels or instructions are provided when content requires user input.

### Robust
- **4.1.2 Name, Role, Value (A)**: user-interface components expose these programmatically and changes are available to user agents.

These are examples, not a complete WCAG checklist. Do not infer a failure merely from an absent visual cue when implementation semantics are unknown.

## Review approach

1. Define the evidence reviewed, target platform, intended standard, and unreviewed states.
2. Inspect perceivable content, contrast evidence, semantic cues, and information presentation.
3. Consider keyboard access, focus visibility/order, predictable behavior, input instructions/errors, and control names/roles/states. Mark implementation questions as unverified when only a mockup is available.
4. For each issue, connect the observed evidence to the exact criterion, explain affected task/users, give a concrete remediation, and state confidence/limitation.
5. Prioritize blockers and common-path barriers before refinements. Recommend automated and manual testing where needed; automated checks alone cannot establish accessibility.

## Output

```markdown
## Accessibility Review: [Design/Page]
**Evidence reviewed:** [screenshot/file/description/version] | **Target:** [platform] | **Reference:** WCAG [version, level] | **Review date:** [date if known]
**Scope and limitations:** [what is and is not established; no unperformed tests implied]

### Summary
[Key risks and review boundary; issue counts only if actually counted]

### Findings
| # | Evidence/element | Barrier and impact | WCAG criterion | Priority | Recommendation | Confidence/limitation |
|---|---|---|---|---|---|---|
| 1 | [specific evidence] | [specific barrier] | [number and exact name/version/level] | [reasoned priority] | [action] | [observed/inferred/unverified] |

### Contrast evidence (only when values support calculation)
| Element | Foreground | Background | Ratio | Applicable requirement | Result/source |
|---|---|---|---|---|---|

### Keyboard/focus and assistive-technology checks
| Flow/control | Evidence available | Not established / test needed |
|---|---|---|

### Priority remediation
1. [Most impactful fix and rationale]
2. [Next fix]

### Follow-up validation
[Specific browser/device, keyboard, screen-reader, zoom, or measurement checks to perform]
```

Do not fill ratio/result cells from visual guesses. Avoid issue totals or conformance labels unless justified by complete scope and actual evidence.