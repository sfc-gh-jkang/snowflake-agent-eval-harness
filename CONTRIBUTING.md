# Contributing

Issues and pull requests are welcome. This is a reference implementation
maintained on a best-effort basis, so please keep changes focused.

## Before opening a PR

```bash
make venv
make test-offline     # what CI runs: 44 tests, no Snowflake connection needed
```

CI runs the offline suite on Python 3.11 and 3.12. Tests requiring a live
Snowflake account skip automatically when no connection is configured, so a PR
does not need account access to be reviewable.

## Two conventions worth knowing

**Every number in the docs is guarded by a test.** `tests/test_07_claims.py`
compares figures written in `README.md` and `docs/DEMO_GUIDE.md` against what a
live account actually reports. If you change a documented number, expect a test
to argue with you — that is the point. Score-band assertions only run when
`SF_ACCOUNT` matches `SF_CLAIMS_ACCOUNT`, so they will not fail on your account
with numbers measured on someone else's.

**The LLM judge is non-deterministic.** Do not add an assertion on an exact
score. Use a band, and say in the docs that it is a band. `docs/GOTCHAS.md`
records 25 traps found while building this; if you hit a new one, adding it there
is a genuinely useful contribution.

## License

Contributions are accepted under Apache-2.0, matching the repository license.
