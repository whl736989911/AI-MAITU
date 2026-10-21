---
name: research-synthesis
description: Synthesize supplied interviews, survey results, usability-test notes, support feedback, or reviews into transparent themes, insights, and next steps. Preserve evidence and methodological limitations.
argument-hint: "<supplied research data, transcripts, or notes>"
---

# Research synthesis

Synthesize only research material supplied by the caller or retrieved from a connector that is actually configured and authorized for the caller and resource. Common inputs include interview transcripts/notes, survey exports, usability-test notes, support feedback, NPS/CSAT responses, and app reviews. If external feedback, analytics, or knowledge-base access is absent, ask for exports or pasted material and state that no external source was queried.

## Evidence discipline

- Preserve study method, date, recruitment and sample facts only as supplied. Do not invent participant counts, quotes, prevalence, segment sizes, percentages, or causal claims.
- Quote only verbatim source text, with supplied participant/source identifiers. Paraphrases must be marked as paraphrases.
- Distinguish raw observation, researcher interpretation, product implication, and recommendation.
- A count over provided records is not automatically representative of the user population; state denominator, missingness, and sampling limits.
- Treat correlations and qualitative reports as hypotheses, not proof of causality.

## Synthesis workflow

1. Inventory the dataset, format, known method/sample/date and gaps.
2. Extract discrete observations with traceable source references (participant, question, record, or row when available).
3. Group similar observations using affinity mapping; retain meaningful counterexamples and divergent cases.
4. Form themes that capture a pattern without overgeneralizing. Track evidence for and against each theme.
5. Turn themes into insights and opportunities; state the reasoning chain and confidence.
6. Prioritize with explicit impact/effort rationale only when inputs support it; identify further research questions and methodological limitations.
7. Use journey mapping or Jobs to Be Done framing only where the supplied evidence supports the needed context.

## Output

```markdown
## Research Synthesis: [Study name]
**Method:** [supplied / unknown] | **Participants/records:** [supplied count or unknown; define denominator]
**Date range:** [supplied / unknown] | **Source materials:** [inventory] | **Limitations:** [sampling, coverage, bias, missing data]

### Executive summary
[Key evidence-backed findings with appropriate uncertainty]

### Key themes
#### Theme 1: [Name]
**Pattern:** [summary]
**Evidence:**
- [Verbatim quote or accurately labeled paraphrase] — [source identifier]
**Counterevidence / variation:** [if present]
**Prevalence:** [count/denominator only if counted from an explicit complete set; otherwise not estimated]
**Interpretation and implication:** [clearly distinguished]

### Insights → opportunities
| Insight and evidence | Opportunity | Impact/effort rationale | Confidence |
|---|---|---|---|

### Segments or needs (only if supported)
| Segment/need | Evidence and characteristics | Limits; size only if measured |
|---|---|---|

### Recommendations
1. **[Priority]** — [action, evidence, and rationale]

### Questions for further research
- [Unresolved question]

### Methodology notes
[How source material was provided/analyzed; limitations, bias, counterexamples, and any unsupported conclusions avoided]
```

## Tips

1. Preserve compelling raw quotes and their provenance; never synthesize a quote.
2. Keep observations separate from interpretation (e.g. observed clicks versus hypothesized confusion).
3. Quantify only reproducible counts from a stated dataset; avoid “most users” without a defensible denominator.
4. If analytics or prior research are available through an authorized configured connector, identify the exact source and keep it distinct from the supplied qualitative evidence. Do not imply validation where none occurred.