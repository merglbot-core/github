# Slack Release Notification Templates

## Pre-Release Announcement

```
📣 **Release Announcement - v[VERSION]**

🗓️ **Scheduled**: [DATE] at [TIME] UTC
🎯 **Type**: [Major/Minor/Patch/Hotfix]
👤 **Release Manager**: @[username]

**Key Changes**:
• [Feature 1]
• [Feature 2]
• [Bug fix 1]

⚠️ **Expected Downtime**: [None/X minutes]
📋 **Rollback Plan**: Ready

Thread for updates 👇
```

## Release Started

```
🚀 **Release v[VERSION] - STARTED**

⏰ Started at: [TIME]
🔄 Progress: Deploying to [staging/production]
📊 Tracking: [Dashboard Link]

Will update when complete...
```

## Release Completed - Success

```
✅ **Release v[VERSION] - COMPLETED**

🎉 Successfully deployed to production!
⏱️ Duration: [X] minutes
📈 All systems operational

**Highlights**:
• ✨ [New feature 1]
• 🐛 [Fixed issue 1]
• ⚡ [Performance improvement]

📄 Full changelog: [GitHub Release Link]
📊 Metrics dashboard: [Dashboard Link]

Great work team! 🙌
```

## Release Completed - With Issues

```
⚠️ **Release v[VERSION] - COMPLETED WITH ISSUES**

✅ Deployed to production
⚠️ Minor issues detected (being monitored)

**Issues**:
• [Issue 1 - Impact: Low]
• [Issue 2 - Under investigation]

**Actions**:
• Monitoring closely
• Hotfix planned for [TIME] if needed

📊 Dashboard: [Link]
🔍 Tracking issue: [GitHub Issue]
```

## Rollback Notification

```
🔄 **ROLLBACK - v[VERSION]**

⚠️ Rolling back to v[PREVIOUS_VERSION]
🕐 Started: [TIME]
👤 Decision by: @[username]

**Reason**: [Brief explanation]

**Impact**:
• [Impact description]
• Estimated recovery: [X] minutes

Updates to follow...
```

## Hotfix Release

```
🚨 **HOTFIX Release - v[VERSION]**

🔥 Urgent fix for: [Issue description]
🎯 Affected services: [List]
⏱️ ETA: [X] minutes

**Fix includes**:
• [Fix description]

**Testing**: Verified in staging
**Risk**: [Low/Medium/High]

Deploying now...
```

## Post-Release Report

```
📊 **Release v[VERSION] - Post-Release Report**

**Metrics (first 24 hours)**:
• ✅ Availability: 99.99%
• 📈 Error rate: 0.01% (within SLA)
• ⚡ Avg response time: 145ms
• 👥 Active users: [X]

**Feedback**:
• Positive responses: [X]
• Issues reported: [X]
• Support tickets: [X]

**Next Steps**:
• Monitor [specific metric]
• Address [minor issue]

Full report: [Link]
```

## Emergency Communication

```
🚨 **URGENT: Production Issue - v[VERSION]**

❌ Critical issue detected
🔧 Team investigating

**Impact**:
• Service: [Affected service]
• Users affected: [Estimate]
• Started: [TIME]

**Actions**:
• Incident response activated
• [Current action]

Updates every 15 minutes
Incident channel: #incident-[NUMBER]
```

---

# Email Templates

## Stakeholder Release Notification

### Subject: Release v[VERSION] - [SUCCESS/COMPLETED/ROLLBACK]

```
Dear Stakeholders,

We are pleased to announce the successful deployment of version [VERSION] to production.

**Release Summary:**
- Release Date: [DATE]
- Release Type: [Major/Minor/Patch]
- Duration: [X] minutes
- Status: Successfully deployed

**Key Deliverables:**
• [Business value 1]
• [Business value 2]
• [Technical improvement]

**Performance Metrics:**
- Deployment success rate: 100%
- Zero downtime achieved
- All acceptance criteria met

**Customer Impact:**
[Description of positive impacts for customers]

**Next Release:**
Scheduled for [DATE] - focusing on [brief description]

For detailed technical information, please refer to our release notes:
[GitHub Release Link]

Please don't hesitate to reach out if you have any questions.

Best regards,
[Release Manager Name]
[Title]
```

## Internal Team Communication

### Subject: Release v[VERSION] Retrospective - Action Items

```
Team,

Following our release of v[VERSION], here's our retrospective summary:

**What Went Well:**
✅ [Success 1]
✅ [Success 2]
✅ [Success 3]

**Areas for Improvement:**
⚠️ [Issue 1] - Owner: @[name]
⚠️ [Issue 2] - Owner: @[name]

**Action Items:**
1. [Action] - Due: [DATE] - Owner: @[name]
2. [Action] - Due: [DATE] - Owner: @[name]

**Release Metrics:**
- Lead time: [X] days
- Deployment frequency: [X]
- MTTR: [X] minutes
- Change failure rate: [X]%

Let's discuss in our next standup.

Thanks,
[Name]
```

---

# Usage Guidelines

## When to Use Each Template

1. **Pre-Release**: 24 hours before scheduled release
2. **Release Started**: As soon as deployment begins
3. **Completed Success**: Within 5 minutes of verification
4. **Completed with Issues**: If non-critical issues detected
5. **Rollback**: Immediately when rollback decision made
6. **Hotfix**: For urgent production fixes
7. **Post-Release Report**: 24 hours after release
8. **Emergency**: For critical production issues

## Customization Tips

- Keep messages concise and scannable
- Use emojis for visual hierarchy
- Always include relevant links
- Tag responsible people
- Provide clear next steps
- Update thread with progress
- Include metrics when available

## Channel Guidelines

- `#releases` - All release notifications
- `#engineering` - Technical details
- `#product` - Feature announcements
- `#support` - Customer-facing changes
- `#incidents` - Production issues