# Makefile for Frontend Tests

.PHONY: install test test-unit test-e2e test-coverage test-watch clean

# Install dependencies
install:
	@echo "Installing dependencies..."
	npm install
	@echo "Installing Playwright browsers..."
	npx playwright install chromium

# Run all tests
test:
	@echo "Running all tests..."
	npm test

# Run only unit tests
test-unit:
	@echo "Running unit tests..."
	npm run test:unit

# Run only E2E tests
test-e2e:
	@echo "Running E2E tests..."
	npm run test:e2e

# Run tests with coverage
test-coverage:
	@echo "Running tests with coverage..."
	npm run test:coverage

# Run tests in watch mode
test-watch:
	@echo "Running tests in watch mode..."
	npm run test:watch

# Clean coverage and test artifacts
clean:
	@echo "Cleaning test artifacts..."
	rm -rf coverage/ .pytest_cache/ test-results/
	find . -name "*.log" -delete

# Run tests against local server
test-local:
	@echo "Running tests against local server..."
	EFP_URL=http://localhost:8000 npm test

# Run specific test file
test-file:
	@echo "Running $(FILE)..."
	npx jest $(FILE)

# Show test status
status:
	@echo "Test environment status:"
	@node --version 2>/dev/null || echo "Node.js: not found"
	@npm --version 2>/dev/null || echo "npm: not found"
	@npx playwright --version 2>/dev/null || echo "Playwright: not found"
