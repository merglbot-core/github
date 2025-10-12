# Security Certification Quiz

## 📋 Quiz Instructions

- **Total Questions:** 20
- **Passing Score:** 80% (16/20 correct)
- **Time Limit:** 30 minutes
- **Open Book:** You may reference training materials
- **Attempts:** 3 maximum

## 🎯 Quiz Questions

### Section 1: Gitignore Security (Questions 1-5)

**Q1. Which files MUST be in .gitignore? (Select ALL that apply)**
- [ ] A) `.env`
- [ ] B) `.env.example`
- [ ] C) `terraform.tfstate`
- [ ] D) `README.md`

**Answer:** A, C

---

**Q2. What should you do FIRST if you accidentally commit a secret to git?**
- [ ] A) Delete the file and push again
- [ ] B) Rotate the secret immediately
- [ ] C) Hope no one notices
- [ ] D) Create a PR to remove it

**Answer:** B

---

**Q3. How long does it typically take for bots to discover leaked API keys on GitHub?**
- [ ] A) 1 week
- [ ] B) 24 hours
- [ ] C) 2-5 minutes
- [ ] D) Instantly

**Answer:** C

---

**Q4. True or False: It's safe to commit `.env.example` files.**
- [ ] True
- [ ] False

**Answer:** True (if they contain only placeholder values, no real secrets)

---

**Q5. Which tool prevents secrets from being committed locally?**
- [ ] A) GitHub Secret Scanning
- [ ] B) git-secrets (pre-commit hook)
- [ ] C) TruffleHog
- [ ] D) All of the above

**Answer:** B

---

### Section 2: Secret Management (Questions 6-10)

**Q6. Where should production secrets be stored?**
- [ ] A) In code files
- [ ] B) In environment variables
- [ ] C) GCP Secret Manager
- [ ] D) Both B and C

**Answer:** D

---

**Q7. What is the correct secret naming convention at merglbot?**
- [ ] A) `api-key`
- [ ] B) `runtime--service--env--name`
- [ ] C) `SECRET_NAME`
- [ ] D) No convention needed

**Answer:** B

---

**Q8. How often should production secrets be rotated?**
- [ ] A) Never
- [ ] B) Every 90 days
- [ ] C) Every year
- [ ] D) Only when compromised

**Answer:** B

---

**Q9. True or False: You can share secret NAMES (not values) with AI coding assistants.**
- [ ] True
- [ ] False

**Answer:** True

---

**Q10. What is the blast radius of a leaked GCP service account key?**
- [ ] A) Only the specific service
- [ ] B) The entire GCP project
- [ ] C) All projects in the organization
- [ ] D) Depends on IAM permissions

**Answer:** D

---

### Section 3: IAM & Access Control (Questions 11-15)

**Q11. What is the principle of least privilege?**
- [ ] A) Give users maximum permissions
- [ ] B) Give users only the permissions they need
- [ ] C) Everyone gets the same permissions
- [ ] D) No one gets any permissions

**Answer:** B

---

**Q12. Which GCP IAM role should a Cloud Run service use?**
- [ ] A) roles/owner
- [ ] B) roles/editor
- [ ] C) Custom role with minimal permissions
- [ ] D) roles/viewer

**Answer:** C

---

**Q13. True or False: Service accounts should have human-readable names.**
- [ ] True
- [ ] False

**Answer:** True

---

**Q14. How should authentication be verified in IAP-protected services?**
- [ ] A) Check `X-Goog-Authenticated-User-Email` header
- [ ] B) Trust all requests
- [ ] C) Use session cookies
- [ ] D) No verification needed

**Answer:** A

---

**Q15. What is WIF (Workload Identity Federation)?**
- [ ] A) A Wi-Fi standard
- [ ] B) OIDC-based auth without service account keys
- [ ] C) A database connection method
- [ ] D) A secret encryption algorithm

**Answer:** B

---

### Section 4: Incident Response (Questions 16-20)

**Q16. What is the FIRST step in a security incident response?**
- [ ] A) Write a report
- [ ] B) Contain the threat (rotate secrets)
- [ ] C) Call your manager
- [ ] D) Delete all logs

**Answer:** B

---

**Q17. Which Slack channel should you notify for security incidents?**
- [ ] A) #general
- [ ] B) #security
- [ ] C) #random
- [ ] D) Don't notify anyone

**Answer:** B

---

**Q18. How long do you have to complete a post-mortem after a security incident?**
- [ ] A) No deadline
- [ ] B) 24 hours
- [ ] C) 1 week
- [ ] D) 1 month

**Answer:** B

---

**Q19. True or False: You should force-push to remove secrets from git history.**
- [ ] True
- [ ] False

**Answer:** True (if no one has pulled yet; otherwise coordinate with team)

---

**Q20. What should you check after rotating a compromised secret?**
- [ ] A) Audit logs for unauthorized access
- [ ] B) All systems using the secret still work
- [ ] C) Secret is properly stored
- [ ] D) All of the above

**Answer:** D

---

## 📊 Scoring Guide

**Scoring:**
- 20/20 = 100% - Outstanding!
- 16-19/20 = 80-95% - Passed
- 12-15/20 = 60-75% - Review materials and retake
- <12/20 = <60% - Mandatory retraining required

## ✅ Certification

Upon passing:
- Certificate issued via email
- Badge added to Slack profile
- Access granted to advanced security modules
- Valid for 6 months

## 🔄 Recertification

- **Frequency:** Every 6 months
- **Format:** Abbreviated quiz (10 questions)
- **Passing Score:** 80%

---

## 📝 Submit Your Quiz

1. Record your answers
2. Submit to: training@merglbot.ai
3. Subject: "Security Certification Quiz - [Your Name]"
4. Include: Employee ID and date completed

---

**Questions about the quiz?** Contact training@merglbot.ai

**Need to retake?** Wait 24 hours between attempts to review materials.
