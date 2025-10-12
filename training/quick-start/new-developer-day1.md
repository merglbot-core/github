# New Developer Onboarding - Day 1 Guide

## 🎉 Welcome to merglbot.ai!

This guide will help you get started on your first day. Follow these steps in order to set up your development environment and complete your first tasks.

## ✅ Pre-Day 1 Checklist

You should have received:
- [ ] GitHub organization invite (merglbot-core)
- [ ] Slack workspace invite  
- [ ] Google Workspace account
- [ ] GCP project access (if applicable)
- [ ] This onboarding guide

## 📅 Day 1 Schedule

| Time | Activity | Duration |
|------|----------|----------|
| 9:00 - 9:30 | Welcome & Team Introductions | 30 min |
| 9:30 - 10:30 | Environment Setup | 60 min |
| 10:30 - 11:00 | Security Basics Workshop | 30 min |
| 11:00 - 12:00 | Codebase Tour | 60 min |
| 12:00 - 13:00 | Lunch Break | 60 min |
| 13:00 - 14:00 | First PR Walkthrough | 60 min |
| 14:00 - 15:00 | Bot Tools Introduction | 60 min |
| 15:00 - 16:00 | Q&A and Documentation | 60 min |
| 16:00 - 17:00 | First Commit & Wrap-up | 60 min |

---

## 🛠️ Part 1: Environment Setup (60 min)

### 1.1 Install Required Tools

```bash
# macOS - using Homebrew
brew install git gh node@20 python@3.11 gcloud

# Install tfenv for version management (recommended)
brew install tfenv

# Verify installations
git --version
node --version
python3 --version
gcloud --version

# Install specific Terraform version (pinned for consistency)
tfenv install 1.6.6
tfenv use 1.6.6
terraform --version  # Should show 1.6.6
```

### 1.2 Configure Git

```bash
# Set your identity
git config --global user.name "Your Name"
git config --global user.email "your.email@merglbot.ai"

# Use main as default branch
git config --global init.defaultBranch main

# Configure editor (optional)
git config --global core.editor "code --wait"  # VS Code
# OR
git config --global core.editor "vim"  # Vim
```

### 1.3 Set Up SSH Keys for GitHub

```bash
# Generate SSH key
ssh-keygen -t ed25519 -C "your.email@merglbot.ai"

# Start ssh-agent
eval "$(ssh-agent -s)"

# Add key to ssh-agent
ssh-add ~/.ssh/id_ed25519

# Copy public key
cat ~/.ssh/id_ed25519.pub
```

**Action Required:** Add the public key to your GitHub account:
https://github.com/settings/keys

### 1.4 Authenticate GitHub CLI

```bash
# Login to GitHub
gh auth login

# Select: GitHub.com
# Select: SSH
# Select: Yes (upload SSH key)
# Authenticate in browser
```

### 1.5 Clone Required Repositories

```bash
# Create projects directory
mkdir -p ~/projects/merglbot
cd ~/projects/merglbot

# Clone main repositories
gh repo clone merglbot-core/platform
gh repo clone merglbot-core/github
gh repo clone merglbot-public/docs

# Navigate to platform repo
cd platform
```

### 1.6 Install Project Dependencies

```bash
# For Node.js projects
cd ~/projects/merglbot/platform
npm install

# For Python projects
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 🔒 Part 2: Security Basics (30 min)

### 2.1 Install Security Tools

```bash
# Install git-secrets
brew install git-secrets

# Install pre-commit
pip install pre-commit
```

### 2.2 Configure Git Secrets

```bash
cd ~/projects/merglbot/platform

# Install hooks
git secrets --install

# Add patterns
git secrets --register-aws
git secrets --add 'ANTHROPIC_API_KEY'
git secrets --add 'sk-ant-[a-zA-Z0-9-]+'
```

### 2.3 Verify .gitignore

```bash
# Check .gitignore exists
cat .gitignore

# Test that secrets are ignored
touch .env
git status  # Should NOT show .env
rm .env
```

**✅ Checkpoint:** Secrets protection is configured!

---

## 📚 Part 3: Codebase Tour (60 min)

```
merglbot-core/platform/
├── apps/                 # Frontend applications
│   ├── portal/          # Main user portal
│   └── admin/           # Admin dashboard
├── services/            # Backend services
│   ├── btf/             # BTF service
│   └── aaas/            # AaaS service
├── packages/            # Shared libraries
├── docs/                # Documentation
└── .github/             # CI/CD workflows
```

### 3.2 Key Concepts

**WARP Standards:** Our internal guidelines for:
- Security best practices
- Bot-assisted development
- Release management

**Tech Stack:**
- **Frontend:** React 18, Vite, MUI v5
- **Backend:** Python 3.11, FastAPI
- **Infrastructure:** GCP (Cloud Run, BigQuery)
- **IaC:** Terraform

### 3.3 Explore the Codebase

```bash
# Browse services
ls -la services/

# Check documentation
cat docs/README.md

# Review workflows
ls -la .github/workflows/
```

---

## 🚀 Part 4: Your First PR (60 min)

### 4.1 Find a Good First Issue

```bash
# List good first issues
gh issue list --label "good first issue" --repo merglbot-core/platform
```

**Alternatively:** Ask your mentor for a starter task

### 4.2 Create a Feature Branch

```bash
cd ~/projects/merglbot/platform

# Create branch
git checkout -b feat/your-first-feature

# Example: Update documentation
echo "## My First Contribution" >> docs/CONTRIBUTORS.md
```

### 4.3 Make Your Changes

```bash
# Edit files with your favorite editor
code docs/CONTRIBUTORS.md

# Check what changed
git status
git diff
```

### 4.4 Commit Your Changes

```bash
# Verify git-secrets is installed first
if ! command -v git-secrets &>/dev/null; then
  echo "⚠️  WARNING: git-secrets not installed!"
  echo "Run: brew install git-secrets"
  exit 1
fi

# Stage changes
git add docs/CONTRIBUTORS.md

# Commit with conventional commits format and error handling
if git commit -m "docs: Add my first contribution to CONTRIBUTORS

- Added my name to contributors list
- Updated documentation structure"; then
  echo "✅ Commit successful"
else
  echo "❌ Commit failed. Possible reasons:"
  echo "  - Pre-commit hook blocked (check git-secrets output above)"
  echo "  - Nothing to commit"
  echo "  - Commit message format invalid"
  exit 1
fi
```

### 4.5 Push and Create PR

```bash
# Push to remote
git push origin feat/your-first-feature

# Create PR
gh pr create \
  --title "docs: Add my first contribution" \
  --body "My first PR to merglbot! 🎉" \
  --label "documentation"
```

**✅ Checkpoint:** Your first PR is created!

---

## 🤖 Part 5: Bot Tools Introduction (60 min)

### 5.1 GitHub Copilot Setup

If you have access to GitHub Copilot:

1. Install VS Code extension: "GitHub Copilot"
2. Authenticate with your GitHub account
3. Test: Open a `.js` file and start typing a function

### 5.2 Cursor IDE (Optional)

If you prefer Cursor:

```bash
# Download from https://cursor.sh
# Install and authenticate

# Try the AI chat feature
# Cmd+K to open Composer
```

### 5.3 Warp AI (Terminal)

```bash
# If using Warp terminal
# Press Cmd+` to activate AI

# Try: "How do I list all git branches?"
```

**⚠️ CRITICAL - AI Tool Safety:**
- ✅ SAFE: Code logic, function names, architecture questions
- ✅ SAFE: Secret NAMES (e.g., "ANTHROPIC_API_KEY")
- ❌ NEVER: Secret VALUES, API keys, tokens
- ❌ NEVER: Customer data, PII, internal URLs
- ❌ NEVER: Full .env files or credentials.json content
- ❌ NEVER: Production database connection strings

---

## 📝 Part 6: First Day Tasks Checklist

Complete these tasks by end of day:

- [ ] Environment fully set up
- [ ] Git configured with SSH keys
- [ ] Security tools installed (git-secrets)
- [ ] Repositories cloned
- [ ] First PR created (documentation)
- [ ] Slack profile completed
- [ ] Team introductions done
- [ ] Attended welcome meeting

---

## 🆘 Need Help?

### Communication Channels

- **Slack:** #onboarding - New hire questions
- **Slack:** #training-questions - Training help
- **Slack:** #platform-support - Technical issues
- **Email:** onboarding@merglbot.ai

### Key Contacts

- **Your Mentor:** (assigned during orientation)
- **Platform Team:** @platform-team on Slack
- **HR:** hr@merglbot.ai

---

## 📚 Next Steps

After completing Day 1:

1. **Day 2-5:** Complete [Security Training Track](../security/01-gitignore-security.md)
2. **Week 2:** [Bot-Driven Development Track](../bots/01-ai-assistants-overview.md)
3. **Week 3:** [Release Management Track](../release/01-process-overview.md) (if applicable)

---

## 🎓 Learning Resources

- [WARP Standards Repository](https://github.com/merglbot-public/docs)
- [Internal Documentation](https://docs.merglbot.ai)
- [Team Wiki](https://wiki.merglbot.ai)
- [Training Videos](https://training.merglbot.ai)

---

## 💡 Tips for Success

1. **Ask Questions** - No question is too small
2. **Document as You Learn** - Help future new hires
3. **Pair Program** - Learn from experienced developers
4. **Attend Office Hours** - Weekly sessions for questions
5. **Take Notes** - You'll reference them later

---

**Welcome to the team! 🎉**

We're excited to have you here. Remember, everyone was new once - don't hesitate to ask for help!

---

**Questions?** Ask in #onboarding on Slack or email onboarding@merglbot.ai
