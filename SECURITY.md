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

The Manager API key is the primary credential this project handles. It is sent in the `X-API-KEY` header on every request to the Manager instance at `MANAGER_API_URL`.

**What a leaked key allows.** The scopes and the permanent denylist in this server limit what this server will do. They do not limit the key. Anyone who holds the key can call the Manager API directly and bypass this server entirely, with whatever access the token has in Manager. This server's scope settings do not narrow the token itself, so unless Manager's own settings say otherwise, treat a leaked key as full access to that business's data, including writes.

**What this server does to reduce exposure.**

- The key is read from the environment and held only in the HTTP client's request header. No tool returns it, and it is not written to tool output, error messages or logs. Automated tests check this with a sentinel key on success, HTTP error and connection error paths, with debug level logging enabled for every logger including `httpx` and `httpcore`.
- The write scopes, delete scopes and denylist stop an AI assistant, or a prompt that manipulates it, from making writes or deletes through a correctly configured server beyond what you enabled. They limit the assistant, not the key. With no scopes set the server registers read-only tools only.

**If you suspect a leak.** Revoke the access token in Manager (Settings, Access Tokens) and create a new one, update the environment that holds it, and review Manager's records for unexpected changes. The local audit log (`MANAGER_MCP_AUDIT_LOG_PATH`) records only corrective writes made through this server, with before and after state. It does not record direct use of the key, and this project has no key rotation feature.

**Reducing the risk.**

- Use a dedicated token for each deployment, so it can be revoked without disturbing others.
- Keep the key out of version control and out of shared client configuration. Store it in the environment of the MCP client or in a private file with restricted permissions.
- Use `https`, or a loopback or private network address, for `MANAGER_API_URL`. The key travels in a header on every request, so a plain `http` URL to a remote host exposes it to the network. The server logs a warning to standard error when `MANAGER_API_URL` is plain `http` to any host other than a loopback address.
- Do not expose the Manager API to the public internet.
- Enable only the scopes your use case needs, to limit what an assistant can do.

If you discover a way to escalate beyond an enabled scope, to bypass the policy denylist, or to obtain the key from tool output, errors or logs, that is a vulnerability under this policy. Please report it as above.

## Dependencies

Third-party dependency licences and versions are tracked in
THIRD-PARTY-NOTICES. Automated dependency vulnerability scanning runs
in CI via the `dependency-audit` workflow (pip-audit), alongside
GitHub's own Dependabot alerts. If you find a dependency vulnerability
not yet caught by either, please report it through the process above
rather than opening a public issue.
