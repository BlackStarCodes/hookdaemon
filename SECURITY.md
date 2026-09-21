# Security Policy

## Supported versions

The project is in pre-release development. Only the `main` branch is
supported for security fixes.

## Reporting a vulnerability

**Do not open a public issue.**

Report privately via GitHub:
[Security advisories](https://github.com/BlackStarCodes/hookdaemon/security/advisories/new).

Include:

- Affected component and version.
- Reproduction steps or proof-of-concept.
- Impact (what an attacker gains).
- Any suggested mitigation.

## What to expect

- **Acknowledgment:** within 3 business days.
- **Initial assessment:** within 7 business days.
- **Fix or mitigation plan:** within 30 days for confirmed issues.
- **Public disclosure:** coordinated with the reporter, typically after a
  fix is released.

## Scope

In scope:

- The API service in this repository.
- HMAC signature generation and verification.
- SSRF protections in the outbound delivery path.
- Authentication and multi-tenant isolation.

Out of scope:

- Denial-of-service via resource exhaustion on a public demo deployment.
- Vulnerabilities in upstream dependencies (report those to the
  dependency's own security program).
- Issues requiring a compromised host or privileged local access.

## Design references

The threat model and existing mitigations are documented in
[`SPEC.md`](./SPEC.md) §8 (HMAC), §9 (SSRF), and §10 (rate limiting).
