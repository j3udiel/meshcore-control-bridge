# Contributing

Thanks for considering a contribution. This project is experimental and
security-sensitive, so small, explicit changes are preferred.

## Workflow

1. Fork the repository.
2. Create a focused branch:

   ```bash
   git switch -c feature/my-change
   ```

3. Install development dependencies:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -e ".[dev]"
   ```

4. Run checks:

   ```bash
   python -m ruff check .
   python -m mypy src
   python -m pytest
   bash scripts/check-secrets.sh
   ```

## Commit Style

Use small commits with clear conventional prefixes when possible:

- `docs:`
- `test:`
- `fix:`
- `feat:`
- `chore:`
- `ci:`

## Pull Requests

Keep pull requests small. Include:

- a concise summary;
- motivation;
- tests;
- security impact;
- documentation updates when behavior changes.

New behavior should normally include tests. Hardware-specific changes must keep
the test suite runnable without MeshCore hardware.

## Proposing a New Command

New commands must declare:

- name;
- aliases;
- syntax;
- description;
- minimum role;
- handler;
- whether confirmation is required;
- tests.

Commands must be explicitly registered. Do not add a shell, arbitrary SSH, raw
subprocess execution from received text, `eval`, or equivalent behavior.

## Proposing a New Transport

New transports must implement the transport interface and keep the command
router transport-agnostic. For MeshCore work, do not invent protocol methods:
link to protocol documentation, a library, or captured behavior that has been
sanitized.

## Proposing a Server Provider

Server providers must use explicit APIs or allow-listed operations. They must
not accept arbitrary paths, hostnames, shell commands, or arguments from
MeshCore messages.

## Security Rules

Never commit:

- tokens;
- private keys;
- MeshCore channel secrets;
- private logs;
- SQLite databases;
- personal node identifiers;
- unredacted network captures;
- real `.env` files;
- real `config.yaml` files.

Use placeholders such as `homeassistant.local`, `192.168.1.50`,
`admin-device`, `meshcore-public-key-or-stable-node-id`, and
`private-control-channel`.
