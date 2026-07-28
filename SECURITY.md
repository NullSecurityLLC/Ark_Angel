# Security Policy

## Supported Versions

Ark Angel is under active development. Security fixes are prioritized for the
`main` branch.

| Version         | Supported        |
| ---------------- | ----------------- |
| `main` (latest)  | ✅ Supported      |
| Pre-release      | ⚠️ Best effort    |
| Legacy builds    | ❌ Not supported  |

Users are strongly encouraged to run the latest version at all times.
Security fixes are not backported to older versions unless deemed critical.

## Reporting a Vulnerability

**Do not open a public issue for a security vulnerability.**

Please report it privately using GitHub's [Security Advisories](https://github.com/NullSecurityLLC/Ark_Angel/security/advisories/new)
feature for this repository (the "Report a vulnerability" button under the
repo's Security tab). This opens a private channel with the maintainers
until a fix is ready.

### What to Include

- A clear description of the vulnerability
- Steps to reproduce the issue
- Potential impact (what could an attacker do?)
- Screenshots, logs, or a proof-of-concept (if available)
- Suggested mitigation (optional, but appreciated)

## Response Timeline

- **Initial acknowledgment:** within 48 hours
- **Status update:** within 5-7 days
- **Resolution target:** depends on severity

Severity levels are internally classified as:

- **Critical** - Immediate action required
- **High** - Significant risk
- **Medium** - Moderate risk
- **Low** - Minimal impact

## Disclosure Policy

- Vulnerabilities are investigated and validated before disclosure.
- Once accepted, a fix is developed and deployed.
- Public disclosure occurs only after a fix is available.
- Reporters are credited unless anonymity is requested.

We do not tolerate irresponsible disclosure that puts users at risk.

## Scope

This policy applies to:

- The core Ark Angel codebase
- Public-facing infrastructure related to Ark Angel
- Official integrations and modules

Out of scope:

- Third-party dependencies (report to their maintainers)
- Social engineering attacks
- Physical access attacks

## Safe Harbor

If you act in good faith and follow this policy:

- You will not face legal action from this project.
- We will treat your research as authorized.
- We will work with you to understand and resolve the issue.

## Final Notes

Ark Angel is a security-focused project by design. We expect both users and
contributors to operate with the same mindset: assume breach, minimize
exposure, act responsibly.
