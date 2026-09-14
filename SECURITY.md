# Security Policy

Thank you for helping keep AlphaLab secure.

Security is an important part of building reliable quantitative research and trading infrastructure. If you discover a security issue, we appreciate responsible disclosure.

---

# Supported Versions

| Version | Supported |
|----------|-----------|
| 3.x | ✅ Yes |
| 2.x | ❌ No |
| < 2.0.0 | ❌ No |

Only the latest stable release receives security updates.

---

# Reporting a Vulnerability

Please **do not open a public GitHub Issue** for security vulnerabilities.

Instead, privately contact the project maintainers with the following information:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested mitigation (if known)
- Proof of concept (if appropriate)

Reports should provide enough information for maintainers to reproduce and investigate the issue.

---

# Responsible Disclosure

We ask that you:

- Allow maintainers reasonable time to investigate.
- Avoid publicly disclosing vulnerabilities before a fix is available.
- Act in good faith to protect project users.

We are committed to investigating all legitimate reports.

---

# Security Scope

Examples of security-related issues include:

- Remote code execution
- Privilege escalation
- Authentication bypass
- Sensitive data exposure
- Dependency vulnerabilities
- Unsafe default behavior
- Arbitrary file access

Two areas are worth naming because AlphaLab implements them itself rather than
taking a dependency:

- **TLS.** `alphalab.common.tls` states a protocol floor of TLS 1.2 explicitly
  rather than inheriting whatever the host's OpenSSL allows, verifies
  certificates and checks hostnames. There is deliberately no parameter that
  lowers the floor and no retry-on-older-protocol fallback. Every outbound
  connection — the venue transport, the WebSocket feed and the provider REST
  client — goes through it.
- **Venue request signing.** `alphalab.broker.transport.HttpVenueTransport` signs
  requests with HMAC-SHA256 and carries a timestamp window and an idempotency
  key.

General bugs, feature requests, and documentation issues should be reported through the normal GitHub issue tracker.

---

# Third-Party Dependencies

**AlphaLab declares no runtime dependencies.** `pyproject.toml` sets
`dependencies = []`, and every package is built on the Python standard library
alone — including the HTTP transport, the RFC 6455 WebSocket client and the HMAC
request signing. There is no third-party code in an installed AlphaLab.

The `[dev]` extra installs a toolchain (`build`, `hatchling`, `mypy`,
`pre-commit`, `pytest`, `pytest-cov`, `ruff`, `twine`) used to develop and
release AlphaLab. It is not installed by `pip install alphalab`, and security
issues originating in those tools should be reported to their upstream
maintainers.

---

# Best Practices

Users are encouraged to:

- Keep AlphaLab updated to the latest stable release.
- Use supported versions of Python, and keep the interpreter patched — since
  AlphaLab has no third-party dependencies, the standard library *is* its
  dependency surface.
- Keep the `[dev]` toolchain updated if you develop against the repository.
- Review configuration before deploying to production.
- Avoid storing credentials directly in source code. AlphaLab accepts venue
  credentials as `VenueCredentials` passed in at the call site and stores none of
  them; `alphalab.enterprise` models principals and holds secret *references*,
  never secret values.

---

# Acknowledgements

We appreciate everyone who responsibly reports security issues and helps improve the reliability and safety of AlphaLab.