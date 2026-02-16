# Frontend Automated Tests

Automated tests for Engineering Flow Platform WebChat UI.

## Prerequisites

- Node.js >= 18
- Running EFP server (default: http://192.168.8.235:8000)

## Installation

```bash
# Install dependencies
npm install

# Install Playwright browsers
npx playwright install chromium
```

## Running Tests

```bash
# Run all tests
npm test

# Run only unit tests
npm run test:unit

# Run only E2E tests
npm run test:e2e

# Run tests with coverage
npm run test:coverage

# Run tests in watch mode
npm run test:watch
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| EFP_URL | http://192.168.8.235:8000 | EFP server URL |
| PLAYWRIGHT_SLOWMO | 0 | Slow down tests (ms) |
| HEADLESS | true | Run in headless mode |

## Test Structure

```
tests/
├── webchat/
│   ├── utils.test.js      # Unit tests for utility functions
│   └── e2e.test.js         # End-to-end tests with Playwright
├── jest.config.js          # Jest configuration
└── package.json           # Dependencies and scripts
```

## Test Categories

### Unit Tests
- `formatSmartDate()` - Date formatting
- `escapeHtml()` - HTML escaping
- `truncateText()` - Text truncation
- `formatTokenCount()` - Token formatting
- `formatCost()` - Cost formatting

### E2E Tests
- Page load and elements
- Typing indicator
- Theme toggle
- Message sending
- Skill selector
- API integration
- CSS validation

## CI Integration

Example GitHub Actions workflow:

```yaml
name: Frontend Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - run: npm test
```

## Writing New Tests

### Unit Test Example
```javascript
describe('myFunction', () => {
  test('should do something', () => {
    const result = myFunction('input');
    expect(result).toBe('expected');
  });
});
```

### E2E Test Example
```javascript
test('feature works', async () => {
  await page.goto('/chat');
  await page.fill('#input', 'test');
  await page.click('#submit');
  await expect(page.locator('.result')).toHaveText('expected');
});
```
