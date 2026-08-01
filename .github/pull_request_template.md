## Summary


## Motivation


## Changes


## Tests

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m mypy src`
- [ ] `bash scripts/check-secrets.sh`

## Security Impact


## Compatibility


## Secrets Checklist

- [ ] No tokens.
- [ ] No private keys.
- [ ] No MeshCore channel secrets.
- [ ] No private logs.
- [ ] No SQLite databases.
- [ ] No personal node identifiers.
- [ ] No unredacted packet captures.

## Documentation Checklist

- [ ] README updated if behavior changed.
- [ ] Docs updated if architecture, security model, or transport behavior changed.
- [ ] Examples remain generic.

## Test Checklist

- [ ] New behavior has tests.
- [ ] Tests do not require real MeshCore hardware.
- [ ] Tests do not require a real Home Assistant instance.
