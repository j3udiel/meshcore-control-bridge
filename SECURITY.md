# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities privately through GitHub Private
Vulnerability Reporting or the repository security advisory workflow.

Do not open public issues for:

- authentication bypasses;
- command execution paths;
- secret disclosure;
- unsafe Home Assistant actions;
- replay or duplicate-command execution;
- privilege escalation;
- channel or sender impersonation.

Do not include secrets in public issues, pull requests, comments, logs, or
screenshots.

## Secrets That Must Never Be Published

- Home Assistant Long-Lived Access Tokens.
- MeshCore channel secrets.
- MeshCore private keys.
- Stable personal node identifiers.
- Private node names.
- Passwords.
- `.env` files.
- Real `config.yaml` files.
- SQLite databases.
- Private logs.
- Unredacted packet captures.

## Supported Versions

This project has not published a stable release yet. Security fixes are expected
to target the default development branch until versioned releases exist.

## Current Limitations

- The real MeshCore transport is not implemented.
- The project has not received a formal security audit.
- Confirmation flow for sensitive actions is not implemented.
- Only read-only MVP commands exist.
- Sender authentication depends on correct configuration of stable MeshCore
  identifiers.
- A private MeshCore channel is not treated as sufficient authentication.

## Security Review Status

No formal security review has been completed. Treat the code as experimental and
review it carefully before running it on any network that can affect real
devices or services.
