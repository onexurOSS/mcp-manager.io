# Security Policy

## Supported versions

Until `v1.0.0` is released, only the current `main` branch is supported. There is no long-term support commitment for pre-release code.

## Reporting a vulnerability

Please do not open a public GitHub issue for a security vulnerability.

Preferred: use GitHub's private vulnerability reporting feature on this
repository. Alternatively, email security@xalterra.com.

Please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce, or a proof of concept if you have one
- The version or commit you tested against

We aim to acknowledge reports within 5 business days and to provide an initial assessment within 14 days. We ask that you give us a reasonable period to address a confirmed issue before any public disclosure.

## Scope

This project is an MCP server that connects an AI assistant to a Manager accounting instance. Security reports relevant to this project include, but are not limited to:

- Authentication or authorisation bypass in the write-policy or scope-checking logic
- A way to perform a write or delete operation without the corresponding scope being enabled
- Exposure of Manager API credentials in logs, error messages, or tool responses
- Injection or traversal issues in any tool that constructs Manager API requests from user or assistant input
- Dependency vulnerabilities with a demonstrable path to exploitation in this project's usage

Out of scope: vulnerabilities in Manager itself (report those to Manager's own security contact), and vulnerabilities in the MCP client (Claude, ChatGPT, or other assistants) rather than this server.

## Threat model: leaked Manager API key

The Manager API key is the primary credential this project handles. If a Manager API key used with this server is leaked:

- **Impact** depends entirely on which scopes were enabled for that key's session. A read-only deployment limits exposure to read access on the connected Manager instance's data. A deployment with write or delete scopes enabled exposes the corresponding write or delete surface.
- **Mitigation available today:** the write-policy denylist (`WritePolicy.authorize`) is checked before any scope, so even a leaked key operating through this server cannot bypass explicitly denied operations. Scopes are additive and independently configured, so a leaked key's blast radius is bounded by whichever scopes were actually enabled, not the server's full capability.
- **What this project does not do:** it does not log every request, and it has no key rotation feature. Corrective writes are recorded, with before and after state, in a local audit log (`MANAGER_MCP_AUDIT_LOG_PATH`). Rotate the key in Manager itself. Treat your Manager API key with the same care as a production database credential: store it outside version control, rotate it if you suspect exposure, and enable only the scopes your use case requires.

If you discover a way to escalate beyond an enabled scope, or to bypass the policy denylist, that is a vulnerability under this policy, please report it as above.

## Dependencies

Third-party dependency licences and versions are listed in `THIRD-PARTY-NOTICES`. GitHub Dependabot alerts are enabled on this repository and are reviewed by the maintainers. Automated dependency scanning inside this project's own CI is planned and is not running yet. If you find a dependency vulnerability with a demonstrable path to exploitation in this project's usage, please report it through the process above rather than opening a public issue.
