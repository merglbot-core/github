# Release Checklist - v[VERSION]

**Release Date**: [YYYY-MM-DD]  
**Release Manager**: @[username]  
**Release Type**: [ ] Major [ ] Minor [ ] Patch [ ] Hotfix  
**Target Environment**: [ ] Staging [ ] Production  

## Pre-Release Preparation

### Code & Testing
- [ ] All feature branches merged to main
- [ ] All tests passing in CI/CD
- [ ] No critical security vulnerabilities in dependencies
- [ ] Performance benchmarks within acceptable range
- [ ] Code coverage meets minimum threshold (>80%)
- [ ] All PR reviews completed and approved

### Documentation
- [ ] CHANGELOG.md updated with all changes
- [ ] API documentation updated (if applicable)
- [ ] README.md version badge updated
- [ ] Migration guides prepared (if breaking changes)
- [ ] Release notes drafted and reviewed

### Security & Compliance
- [ ] Security scan completed (no high/critical issues)
- [ ] Dependency audit performed (`npm audit`, `pip audit`)
- [ ] Secrets rotation scheduled (if needed)
- [ ] GDPR/compliance requirements verified
- [ ] License files updated

## Release Execution

### Version Management
- [ ] Version bumped in appropriate files:
  - [ ] package.json / pyproject.toml
  - [ ] Helm charts / Docker tags
  - [ ] Terraform modules
- [ ] Git tag created and signed: `v[VERSION]`
- [ ] Tag pushed to remote repository

### Build & Artifacts
- [ ] Production build successful
- [ ] Docker images built and scanned
- [ ] Artifacts uploaded to registry
- [ ] Build artifacts signed (if applicable)

### Deployment
- [ ] Deployment to staging environment
- [ ] Smoke tests passed in staging
- [ ] Deployment to production environment
- [ ] Health checks passing
- [ ] Monitoring/alerting verified

## Post-Release Verification

### Functional Validation
- [ ] Critical user journeys tested
- [ ] API endpoints responding correctly
- [ ] Database migrations completed successfully
- [ ] Background jobs running normally
- [ ] Third-party integrations working

### Performance & Monitoring
- [ ] Response times within SLA
- [ ] Error rates below threshold (<1%)
- [ ] Resource utilization normal
- [ ] Logs showing no critical errors
- [ ] Metrics dashboard updated

### Communication
- [ ] Release notes published on GitHub
- [ ] Slack announcement sent to #releases
- [ ] Customer-facing changelog updated
- [ ] Support team notified
- [ ] Stakeholders informed via email

## Rollback Preparation

### Rollback Plan
- [ ] Previous version identifier documented: `v[PREVIOUS]`
- [ ] Rollback procedure documented and tested
- [ ] Database rollback scripts prepared (if needed)
- [ ] Feature flags configured for gradual rollout
- [ ] Rollback decision criteria defined

### Emergency Contacts
- **On-Call Engineer**: @[username] - [phone]
- **Release Manager**: @[username] - [phone]
- **Product Owner**: @[username] - [email]
- **Security Team**: @[team] - [channel]

## Sign-offs

### Required Approvals
- [ ] Engineering Lead: @[username] - [date/time]
- [ ] QA Lead: @[username] - [date/time]
- [ ] Security Team: @[username] - [date/time]
- [ ] Product Owner: @[username] - [date/time]

## Notes & Issues

### Known Issues
- List any known issues or limitations

### Follow-up Actions
- List any post-release tasks or improvements

### Lessons Learned
- Document any issues encountered and how they were resolved

---

## Release Metrics

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Build Time | <10 min | [X] min | [✅/❌] |
| Deployment Time | <5 min | [X] min | [✅/❌] |
| Rollback Time | <2 min | N/A | [✅/❌] |
| Error Rate | <1% | [X]% | [✅/❌] |
| Response Time (p95) | <200ms | [X]ms | [✅/❌] |
| Availability | >99.9% | [X]% | [✅/❌] |

## Release Log

```
[timestamp] - Release started
[timestamp] - Pre-release checks completed
[timestamp] - Build and tests completed
[timestamp] - Deployed to staging
[timestamp] - Staging validation completed
[timestamp] - Deployed to production
[timestamp] - Production validation completed
[timestamp] - Release completed successfully
```

---

**Release Status**: [ ] In Progress [ ] Completed [ ] Rolled Back

**Final Notes**: 
[Add any additional comments or observations about the release]