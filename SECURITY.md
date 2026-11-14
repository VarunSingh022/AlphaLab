# Security Policy

Thank you for helping keep AlphaLab secure.

Security is an important part of building reliable quantitative research and trading infrastructure. If you discover a security issue, we appreciate responsible disclosure.

---

# Supported Versions

| Version | Supported |
|----------|-----------|
| 4.x | ✅ Yes |
| 3.x | ❌ No |
| < 3.0.0 | ❌ No |

Only the latest stable release receives security updates.

The table marked 3.x as the supported line until the 4.0.0 release corrected it;
a test now holds it to the package's major version.

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

One area is worth naming because AlphaLab implements it itself rather than
taking a dependency:

- **TLS.** `alphalab.common.tls` states a protocol floor of TLS 1.2 explicitly
  rather than inheriting whatever the host's OpenSSL allows, verifies
  certificates and checks hostnames. There is deliberately no parameter that
  lowers the floor and no retry-on-older-protocol fallback. Every encrypted
  connection the library makes uses it: an `https://` request from its HTTP
  transport (`alphalab.marketdata.transport`) and a `wss://` connection from its
  RFC 6455 WebSocket client (`alphalab.marketdata.websocket`). Both also accept
  an unencrypted `http://` or `ws://` URL, which TLS does not cover; the URL is
  the caller's choice. Neither opens anything else. The HTTP transport refuses
  `file://`, `ftp://`, `data:` and every other URL that is not `http://` or
  `https://` before opening it, and follows a redirect only to one of those
  two (since v4.3, SEC-002: until then a `file://` URL returned the local
  file's bytes as a provider's response). The WebSocket client has always
  refused anything but `ws://` and `wss://`.

Venue request signing is not in the library. The HMAC-signed venue transport
and its credentials (`HttpVenueTransport`, `VenueCredentials`) left
`alphalab.broker` in v3.11 (ledger BRK-007) and are the reference adapter under
`tests/reference_adapter`, which ships with the source distribution's tests and
is not installed with the package. A venue adapter, its request signing and its
credentials are the host application's.

General bugs, feature requests, and documentation issues should be reported through the normal GitHub issue tracker.

---

# Trust Boundaries

AlphaLab runs in the caller's process with the caller's privileges and
sandboxes nothing.

**A task path selects code to execute** (ledger BDY-028).
`alphalab.cloud_research` runs a job by importing the module its `task_path`
names and calling the callable it names with the job's `kwargs`: `resolve_task`
and `run_task` do, for the jobs `submit_research_job` and
`submit_parameter_sweep` queue. Nothing restricts which importable module or
callable a path may name. **`task_path` and `kwargs` must never come from
untrusted input**: whoever chooses them chooses the code a worker runs, with the
host's privileges. A task's return value reaches the caller through the
executor the caller supplies -- a process pool pickles it -- so a worker is
inside the caller's trust boundary as well.

---

# Third-Party Dependencies

**AlphaLab declares no runtime dependencies.** `pyproject.toml` sets
`dependencies = []`, and every package is built on the Python standard library
alone — including the HTTP transport and the RFC 6455 WebSocket client. There is
no third-party code in an installed AlphaLab.

The `[dev]` extra installs a toolchain (`build`, `hatchling`, `mypy`,
`pre-commit`, `pytest`, `pytest-cov`, `ruff`, `twine`) used to develop and
release AlphaLab. It is not installed with the library itself -- an install
without the `[dev]` extra, as `docs/INTEGRATION.md` describes -- and security
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
- Avoid storing credentials directly in source code. AlphaLab holds no venue
  credential and defines no credential type: a venue adapter and its credentials
  are the host application's (`tests/reference_adapter` shows one). Principals,
  roles and secret storage are the host application's too (`alphalab.enterprise`,
  which held secret *references*, moved out with it in v3.11, ADR-0046).
- Never build a `cloud_research` job's `task_path` or `kwargs` from untrusted
  input (*Trust Boundaries*, above).

---

# Acknowledgements

We appreciate everyone who responsibly reports security issues and helps improve the reliability and safety of AlphaLab.