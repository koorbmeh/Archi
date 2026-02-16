# Archi Test Suite

## Running Tests

### Unit tests (fast, isolated)
```bash
pytest tests/unit/
```

### Integration tests
```bash
pytest tests/integration/
```

### All tests
```bash
pytest
```

### Gate A validation
After running Archi for ~30 min, validate Gate A criteria:
```bash
pytest tests/integration/test_gate_a.py -v
```

### Component scripts (standalone, run manually)
These are integration-style scripts that run for a duration or require manual verification:
```bash
python tests/scripts/test_dream_cycle.py
python tests/scripts/test_computer_use.py
# etc.
```

### V2 pipeline (live API tests)
These test the full message-processing pipeline end-to-end.
They call OpenRouter (~$0.004 per run), so they're marked `@pytest.mark.live`.

```bash
# Run the v2 pipeline tests
pytest tests/integration/test_v2_pipeline.py -v

# Skip live tests when running the full suite
pytest -m "not live"
```

There's also a standalone harness with CLI options (quick mode, category filter, dry-run):

```bash
python test_harness.py              # Full 13-test suite
python test_harness.py --quick      # 5 smoke tests
python test_harness.py --category model   # Only model/* tests
python test_harness.py --dry-run    # Show tests without running
```

### With coverage
```bash
pytest --cov=src --cov-report=html
```

## Test Organization

- `tests/unit/` - Fast, isolated tests of individual components
- `tests/integration/` - Tests of multiple components working together
- `tests/integration/test_v2_pipeline.py` - **Live API tests** for the v2 message pipeline
- `tests/scripts/` - Component/feature scripts (can be run manually, may run for extended time)
- `test_harness.py` - Standalone v2 pipeline test runner (same codepath, CLI-friendly)
