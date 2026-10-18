# Tests

```
tests/
├── unit/           # fast, offline, synthetic-data tests for individual examples
├── integration/    # pipeline/chain tests against the real catalog and fundamentals store
└── snapshots/      # recorded output used by a few tests as a golden reference
```

Run the suite from the repo root, after the editable install described in the root
[README](../README.md#install):

```bash
pytest -q                      # full suite
pytest -q -m "not slow"        # skip long-running tests
pytest -q tests/unit           # unit tests only
```

Most unit tests load the corresponding numbered script under `examples/` with
`importlib.util.spec_from_file_location` and exercise it directly, rather than importing it as
a package — the scripts are examples, not library modules. A few `tests/integration` tests need
the real 1D Parquet catalog, `fundamentals.duckdb`, or a sibling `honba-strategies` checkout; they
skip (rather than fail) when that data or checkout is not available.
