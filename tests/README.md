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

### With coverage
```bash
pytest --cov=src --cov-report=html
```

## Test Organization

- `tests/unit/` - Fast, isolated tests of individual components
- `tests/integration/` - Tests of multiple components working together
- `tests/scripts/` - Component/feature scripts (can be run manually, may run for extended time)
