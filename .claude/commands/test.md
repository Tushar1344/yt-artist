Run the full test suite with coverage:

```
python -m pytest tests/ -v --tb=short --cov=yt_artist --cov-report=term-missing
```

If tests fail:
1. Show failure details
2. Read failing test + source it tests
3. Diagnose root cause
4. Propose fix (don't apply without asking)

If all pass: brief summary with total, passed, coverage %.
