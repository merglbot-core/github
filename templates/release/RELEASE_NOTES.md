# Release Notes - v[VERSION]

**Release Date**: [YYYY-MM-DD]  
**Release Type**: [Major/Minor/Patch/Hotfix]  

## 🎯 Release Overview

Brief summary of what this release accomplishes and why it matters to users.

## ✨ What's New

### Major Features
- **[Feature Name]**: Description of the feature and its benefits
- **[Feature Name]**: Description of the feature and its benefits

### Improvements
- Enhanced [component] for better [benefit]
- Optimized [process] resulting in [improvement]
- Updated [dependency] to version [X.Y.Z]

## 🐛 Bug Fixes
- Fixed issue where [description of problem] ([#issue])
- Resolved [component] error when [condition] ([#issue])
- Corrected [behavior] in [feature] ([#issue])

## 🔧 Technical Improvements
- Refactored [component] for better maintainability
- Added comprehensive tests for [feature]
- Improved CI/CD pipeline performance by [X]%
- Reduced bundle size by [X]KB

## 📊 Performance Enhancements
- API response time improved by [X]%
- Database query optimization reducing load by [X]%
- Memory usage reduced by [X]% in [component]

## ⚠️ Breaking Changes
> **Note**: This section only appears in major releases

- **[Component]**: [Description of breaking change]
  - **Migration Required**: [Steps to migrate]
  - **Deprecated**: [What is deprecated]
  - **Alternative**: [Recommended alternative]

## 🔄 Migration Guide
> **Note**: Include if there are breaking changes or significant updates

### Before Upgrading
1. Backup your data
2. Review breaking changes above
3. Test in staging environment

### Upgrade Steps
1. [Step 1]
2. [Step 2]
3. [Step 3]

### After Upgrading
1. Verify [component] functionality
2. Check [integration] connections
3. Monitor logs for any errors

## 📦 Dependencies

### Updated
- `package-name`: v1.2.3 → v1.3.0
- `another-package`: v2.1.0 → v2.2.0

### Added
- `new-package`: v1.0.0 - For [purpose]

### Removed
- `deprecated-package`: No longer needed

## 🔐 Security Updates
- Patched [CVE-YYYY-XXXXX] in [component]
- Updated [dependency] to address security vulnerability
- Enhanced [security feature] implementation

## 📋 Known Issues
- [Issue description] - Workaround: [steps]
- [Issue description] - Fix planned for v[NEXT_VERSION]

## 🙏 Acknowledgments
Special thanks to our contributors:
- @[username] - [contribution]
- @[username] - [contribution]
- @[username] - [contribution]

## 📖 Documentation
- Updated [documentation section]
- Added guides for [new feature]
- Improved API documentation

## 🔗 Useful Links
- Full Changelog: <ADD_URL_HERE>
- Documentation: <ADD_URL_HERE>
- Migration Guide: <ADD_URL_HERE>
- API Reference: <ADD_URL_HERE>

## 📈 Release Statistics
- **Total Commits**: [X]
- **Files Changed**: [X]
- **Contributors**: [X]
- **Issues Closed**: [X]
- **PRs Merged**: [X]

## 🎉 Community Highlights
- [Community contribution or feedback]
- [User success story]
- [Notable adoption]

---

## Docker Images
```bash
# Pull the latest image
docker pull ghcr.io/merglbot-core/[service]:v[VERSION]

# Or using specific digest for reproducibility
docker pull ghcr.io/merglbot-core/[service]@sha256:[DIGEST]
```

## Installation

### npm/yarn
```bash
npm install @merglbot/[package]@[VERSION]
# or
yarn add @merglbot/[package]@[VERSION]
```

### pip
```bash
pip install merglbot-[package]==[VERSION]
```

### Helm
```bash
helm upgrade --install [release-name] merglbot/[chart] --version [VERSION]
```

## Upgrade Instructions

### From v[PREVIOUS_MAJOR].x
See Migration Guide: <ADD_URL_HERE> for detailed upgrade instructions.

### From v[CURRENT_MAJOR].[PREVIOUS_MINOR].x
No special steps required. Standard upgrade process applies.

---

## Feedback
We'd love to hear your feedback! Please report any issues or suggestions:
- 🐛 [Report a Bug](https://github.com/merglbot-core/[repo]/issues/new?template=bug_report.md)
- 💡 [Request a Feature](https://github.com/merglbot-core/[repo]/issues/new?template=feature_request.md)
- 💬 [Join Discussion](https://github.com/merglbot-core/[repo]/discussions)

## Support
- 📧 Email: support@merglbot.ai
- 💬 Slack: #support
- 📚 Documentation: [docs.merglbot.ai](https://docs.merglbot.ai)

---

**Thank you for using [Product Name]!** 🚀