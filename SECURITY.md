# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| v1.5.x  | Yes       |
| < v1.5  | No        |

## Reporting a Vulnerability

**Please do not open public issues for security vulnerabilities.**

Instead, use GitHub's [private vulnerability reporting](https://github.com/coaxk/maparr/security/advisories/new) to submit your report. You'll get a direct, private channel with the maintainers.

### What to include

- Description of the vulnerability
- Steps to reproduce
- Affected version(s)
- Impact assessment (what an attacker could do)

### What to expect

- Acknowledgement within 48 hours
- Status update within 7 days
- We'll coordinate disclosure timing with you

## Security Measures

MapArr includes several built-in protections:

- **SSRF prevention** — DOCKER_HOST validated against regex allowlist
- **Path traversal protection** — all file writes boundary-checked against stacks directory
- **Rate limiting** — write endpoints capped at 10 requests/minute per IP
- **SSE connection limits** — per-IP cap prevents resource exhaustion
- **Secret redaction** — diagnostic exports strip sensitive values
- **Input validation** — all user-supplied paths and URLs validated before use

See the [Architecture wiki](https://github.com/coaxk/maparr/wiki/Architecture) for the full security model.
