Run tests. If $ARGUMENTS is provided, run only that module (e.g. "storage" runs test_storage.py).

If $ARGUMENTS is empty:
```
python -m pytest tests/ -v --tb=short --cov=yt_artist --cov-report=term-missing
```

If $ARGUMENTS is a module name:
```
python -m pytest tests/test_$ARGUMENTS.py -v --tb=short --cov=yt_artist.$ARGUMENTS
```

If tests fail:
1. Read failing test + source it tests
2. Diagnose root cause
3. Fix it
4. Re-run to verify the fix
5. Show what changed

If all pass: brief summary with total, passed, coverage %.
