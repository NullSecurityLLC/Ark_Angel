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

## What it touches

ArkAngel writes only to its data folder, files named with `--out`, and its own temp folder. It
refuses operating-system folders (`ark_angel/safety.py`), even when run as Administrator or root.
The release workflow runs each built executable in a sandbox to prove it.

## Fake copies

Official release files are signed with GitHub build attestations. To check one, run
`gh attestation verify FILE --repo NullSecurityLLC/Ark_Angel` (see the README). Please report
copies, websites or extensions that pass themselves off as ArkAngel through the same private
advisory form. The name and logo policy is in [TRADEMARKS.md](TRADEMARKS.md).

## Untrusted input, by design

Most of what ArkAngel handles was written by strangers: tips, emails, chat
exports, photos, web pages the Recorder captured, and names in OpenStreetMap.
Evidence is stored exactly as it arrived, and made safe on the way out for
wherever it's shown. Reports of anything that slips through are very welcome:

- **Terminal output:** no escape sequences, so text can't rewrite the screen,
  retitle the window, fake links or write to the clipboard. No bidirectional
  overrides either.
- **HTML report and website:** everything is HTML-escaped, under a strict
  Content-Security-Policy.
- **Markdown reports:** links, images and HTML are escaped, so nothing in a
  preview loads from someone else's server.
- **CSV exports:** no cell can run as a spreadsheet formula.
- **KML and GeoJSON:** no markup in pin names or details.
- **Parsers:** malformed photo metadata is ignored instead of crashing a tip.
  Text extraction runs in linear time on hostile input.
- **Captured URLs:** tokens, sign-in codes and embedded passwords are removed
  before they're stored.

## Final Notes

Ark Angel is a security-focused project by design. We expect both users and
contributors to operate with the same mindset: assume breach, minimize
exposure, act responsibly.
