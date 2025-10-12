# Module 1.1: WARP Gitignore Security

## 🎯 Learning Objectives

By the end of this module, you will be able to:
- Understand why `.gitignore` security is critical
- Identify sensitive files that must never be committed
- Configure project `.gitignore` files correctly
- Use pre-commit hooks to prevent accidental commits
- Respond to security incidents involving committed secrets

## ⏱️ Duration
30 minutes (15 min lecture + 15 min hands-on)

---

## 📋 Why Gitignore Security Matters

### Real-World Impact

**Case Study: AWS Key Leak (2023)**
- Developer committed `.env` file with AWS credentials
- Automated bot scanned GitHub and found keys within 2 minutes
- Result: $50,000 in fraudulent EC2 instances launched
- **Lesson:** One mistake can cost thousands

### What Happens When Secrets Leak?

1. **Immediate Exploitation** - Bots scan GitHub 24/7
2. **Data Breach** - Access to production systems
3. **Financial Loss** - Unauthorized resource usage
4. **Reputation Damage** - Loss of customer trust
5. **Legal Consequences** - GDPR fines, compliance violations

---

## 🔒 Sensitive Files to NEVER Commit

### Environment Files
```bash
# MUST be in .gitignore
.env
.env.local
.env.*.local
.env.production
config.local.js
```

### Credentials & Keys
```bash
# MUST be in .gitignore
*.key
*.pem
*.p12
*.pfx
credentials.json
service-account-*.json
```

### Terraform State
```bash
# MUST be in .gitignore
terraform.tfstate
terraform.tfstate.backup
*.tfvars  # May contain secrets
.terraform/
```

### IDE & OS Files
```bash
# SHOULD be in .gitignore
.vscode/
.idea/
*.swp
.DS_Store
Thumbs.db
```

---

## ✅ Correct .gitignore Configuration

### Template for merglbot.ai Projects

```gitignore
# Secrets and Environment
.env
.env.*
!.env.example
*.key
*.pem
credentials*.json
service-account-*.json

# Terraform
terraform.tfstate*
*.tfvars
.terraform/
.terraformrc
terraform.rc

# Build Artifacts
dist/
build/
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# Dependencies
node_modules/
vendor/
__pycache__/
*.pyc
.venv/
venv/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db
desktop.ini

# Testing
coverage/
.nyc_output/
*.test.log

# Docker (if contains secrets)
docker-compose.override.yml
```

---

## 🛠️ Hands-On Exercise

### Exercise 1: Configure Your Project

**Task:** Add correct `.gitignore` to your project

```bash
# Step 1: Navigate to your project
cd ~/projects/my-merglbot-project

# Step 2: Download template
curl -o .gitignore https://raw.githubusercontent.com/merglbot-core/github/main/.gitignore.template

# Step 3: Verify sensitive files are ignored
git status

# Step 4: Commit the .gitignore
git add .gitignore
git commit -m "chore: Add comprehensive .gitignore for security"
```

**Expected Output:** No `.env`, `*.key`, or `terraform.tfstate` files should appear in `git status`

### Exercise 2: Test Gitignore

```bash
# Create test files
touch .env
touch terraform.tfstate
touch credentials.json

# Check git status - these should NOT appear
git status

# Clean up
rm .env terraform.tfstate credentials.json
```

**✅ Success Criteria:** None of the test files appear in `git status`

---

## 🚨 Pre-Commit Hooks

### Why Pre-Commit Hooks?

Pre-commit hooks prevent secrets from being committed **before** they reach the repository.

### Install git-secrets

```bash
# macOS
brew install git-secrets

# Linux
git clone https://github.com/awslabs/git-secrets.git
cd git-secrets
sudo make install
```

### Configure git-secrets

```bash
# Navigate to your project
cd ~/projects/my-merglbot-project

# Install hooks
git secrets --install

# Add AWS pattern
git secrets --register-aws

# Add custom patterns for merglbot
git secrets --add 'ANTHROPIC_API_KEY'
git secrets --add 'sk-ant-[a-zA-Z0-9-]+'
git secrets --add 'projects/[0-9]+/serviceAccounts/[^"]*'
```

### Test Pre-Commit Hook

```bash
# Try to commit a secret (should fail)
echo "ANTHROPIC_API_KEY=sk-ant-test123" > test.txt
git add test.txt
git commit -m "test"

# Expected: ❌ Commit blocked by git-secrets
# Clean up
git reset HEAD test.txt
rm test.txt
```

---

## 🔍 Secret Scanning

### GitHub Secret Scanning

GitHub automatically scans for:
- API keys
- OAuth tokens
- Private keys
- Database connection strings

**Action Required:** Enable in repository settings

### Pre-Push Scan

```bash
# Scan before pushing
git log -p | grep -E '(password|secret|key|token|api)' -i

# Better: Use automated tool
npm install -g @trufflesecurity/trufflehog
trufflehog git file://. --only-verified
```

---

## 🚨 Incident Response: What If You Commit a Secret?

### Immediate Actions (First 5 Minutes)

```bash
# 1. ROTATE THE SECRET IMMEDIATELY
# GCP Secret Manager
gcloud secrets versions disable latest --secret="leaked-secret-name"
gcloud secrets versions add "leaked-secret-name" --data-file=new_secret.txt

# GitHub Token
# Go to https://github.com/settings/tokens and revoke

# 2. Remove from git history using a modern tool
# First, install git-filter-repo if you haven't:
# python3 -m pip install git-filter-repo
git filter-repo --path path/to/secret/file --invert-paths

# 3. Force push (coordinate with your team!)
git push origin --force --all
git push origin --force --tags
```

### Full Incident Response Checklist

- [ ] Rotate secret immediately
- [ ] Audit logs for unauthorized access
- [ ] Notify #security channel
- [ ] Create incident report
- [ ] Update .gitignore to prevent recurrence
- [ ] Post-mortem within 24 hours

---

## 📊 Quiz: Check Your Knowledge

1. **True/False:** It's safe to commit `.env.example` files
   - **Answer:** ✅ True (if they contain only placeholder values)

2. **What should you do if you accidentally commit an API key?**
   - A) Delete the file and commit again
   - B) Immediately rotate the key and remove from git history
   - C) Hope no one notices
   - **Answer:** ✅ B

3. **Which files MUST be in .gitignore?**
   - A) `.env`
   - B) `terraform.tfstate`
   - C) `*.key`
   - D) All of the above
   - **Answer:** ✅ D

---

## 🎓 Certification Criteria

To pass this module:
- [ ] Complete hands-on exercises
- [ ] Configure .gitignore in your project
- [ ] Install and test git-secrets
- [ ] Score 80%+ on final quiz

---

## 📚 Additional Resources

- [WARP Gitignore Security Standard](https://github.com/merglbot-public/docs/WARP_GITIGNORE_SECURITY.md)
- [git-secrets Documentation](https://github.com/awslabs/git-secrets)
- [GitHub Secret Scanning](https://docs.github.com/en/code-security/secret-scanning)
- [TruffleHog - Find Secrets](https://github.com/trufflesecurity/trufflehog)

---

**Next Module:** [Secret Management Best Practices](./02-secret-management.md)

---

**Questions?** Ask in #security or #training-questions on Slack
