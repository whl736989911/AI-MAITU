---
name: deploy-checklist
description: Prepare a tailored pre-deployment readiness, execution, post-deployment, and rollback checklist from supplied release context.
argument-hint: "[service or release]"
---

# Deployment Readiness Checklist

Produce a checklist for human operators; this skill does not execute deployments, validate CI, monitor production, or approve a release. Mark every item as verified only when evidence is supplied or actually observed through a configured, authorized tool. Otherwise leave unchecked or mark unknown.

## Template

```markdown
## Deploy Checklist: [service/release or TBD]
**Date:** [date or TBD] | **Deployer:** [provided name or TBD]
**Scope / change:** [source or TBD]

### Pre-deploy
- [ ] Tests and required CI evidence reviewed
- [ ] Code review and required approvals confirmed
- [ ] Known critical defects and release scope assessed
- [ ] Database migrations reviewed, tested, and rollback/forward strategy understood (if applicable)
- [ ] Feature flags and configuration reviewed (if applicable)
- [ ] Dependencies, compatibility, capacity, and security impacts considered
- [ ] Rollback / recovery plan documented and actionable
- [ ] On-call and stakeholder communication plan confirmed

### Deploy (operator-run)
- [ ] Staging or pre-production verification completed (if available)
- [ ] Smoke checks and key user flows defined
- [ ] Production rollout approach identified (canary/rolling/other, if applicable)
- [ ] Monitoring signals and observation window agreed from actual baselines

### Post-deploy
- [ ] Key flows and health signals checked by an authorized operator
- [ ] Metrics compared with an identified baseline; no baseline supplied = unknown
- [ ] Release notes and stakeholder updates handled as approved
- [ ] Follow-up items recorded through an authorized process

### Rollback / stop triggers
- [ ] Explicit thresholds defined from service objectives/baselines (do not invent X values)
- [ ] Critical flow failure criteria defined
- [ ] Decision-maker and rollback procedure confirmed

### Open questions / evidence gaps
- [ … ]
```

## Tailoring

Add migration-specific checks for data changes, consumer coordination for breaking API changes, flag owner and cleanup criteria for feature flags, and security/privacy review where scope requires. Agree rollback criteria before release; do not assume a generic observation period or numeric threshold.

## Evidence and authority

CI/CD, source control, monitoring, and release information may be consulted only if a corresponding tool is actually configured and the caller authorized. Otherwise request build/test reports, release diff, current baselines, rollout plan, and relevant runbooks. No automatic verification, baselines, stakeholder notification, ticket update, or deployment is promised. Consequential release actions require explicit approval and a real authorized mechanism.