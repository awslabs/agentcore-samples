# SRE Agent - Quality Assurance Makefile
# This Makefile provides standardized commands for code quality checks

.PHONY: help quality format lint lint-fix typecheck security test install-dev clean

# Default target
.DEFAULT_GOAL := help

help:
	@echo "SRE Agent Quality Assurance Commands"
	@echo "======================================"
	@echo ""
	@echo "Quality Checks:"
	@echo "  quality     - Run all quality checks (format, lint, typecheck, security)"
	@echo "  format      - Format code with black"
	@echo "  lint        - Lint code with ruff"
	@echo "  lint-fix    - Auto-fix linting issues with ruff"
	@echo "  typecheck   - Type check with mypy"
	@echo "  security    - Security scan with bandit"
	@echo ""
	@echo "Testing:"
	@echo "  test        - Run pytest tests"
	@echo ""
	@echo "Development:"
	@echo "  install-dev - Install development dependencies"
	@echo "  clean       - Clean build artifacts and cache"
	@echo ""

# Main quality target - runs all quality checks
quality: format lint typecheck security
	@echo "✅ All quality checks completed successfully!"

# Code formatting with black
format:
	@echo "🎨 Formatting code with black..."
	uv run black .
	@echo "✅ Code formatting complete"

# Linting with ruff
lint:
	@echo "🔍 Linting code with ruff..."
	uv run ruff check .
	@echo "✅ Linting complete"

# Auto-fix linting issues with ruff
lint-fix:
	@echo "🔧 Auto-fixing linting issues with ruff..."
	uv run ruff check . --fix
	@echo "✅ Auto-fix complete"

# Type checking with mypy
typecheck:
	@echo "🔎 Type checking with mypy..."
	uv run mypy .
	@echo "✅ Type checking complete"

# Security scanning with bandit
security:
	@echo "🔒 Security scanning with bandit..."
	uv run bandit -r . -f console
	@echo "✅ Security scan complete"

# Run tests
test:
	@echo "🧪 Running tests with pytest..."
	uv run pytest
	@echo "✅ Tests complete"

# Install development dependencies
install-dev:
	@echo "📦 Installing development dependencies..."
	uv sync --dev
	@echo "✅ Development dependencies installed"

# Clean build artifacts and cache
clean:
	@echo "🧹 Cleaning build artifacts and cache..."
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	@echo "✅ Clean complete"