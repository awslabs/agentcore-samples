# Development

## Running Tests

The SRE Agent includes comprehensive test coverage to ensure reliability:

```bash
# Run all tests
pytest

# Run tests with coverage report
pytest --cov=sre_agent --cov-report=html
open htmlcov/index.html  # View coverage report

# Run specific test categories
pytest tests/unit/          # Fast unit tests
pytest tests/integration/   # Integration tests with mocked APIs
pytest tests/e2e/          # End-to-end tests with demo backend

# Run tests in parallel for speed
pytest -n auto

# Run with verbose output for debugging
pytest -vv -s
```

## Code Quality

Maintain code quality using automated tools:

```bash
# Check type hints with mypy
mypy sre_agent/

# Lint code with ruff
ruff check sre_agent/

# Run all quality checks
make quality
```

