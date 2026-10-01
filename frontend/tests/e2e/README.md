# E2E Tests for MVP Pipeline

This directory contains End-to-End tests for the LLM Security Scanner MVP pipeline using Playwright.

## Test Coverage

The E2E tests cover the complete MVP pipeline:

1. **Authentication Flow**
   - Login with valid API key
   - Error handling for invalid API key
   - Logout functionality

2. **Dashboard**
   - KPI cards display (requests/min, block rate, avg latency, cache hit rate)
   - Recent activity table
   - Live metrics updates

3. **Test Page**
   - Prompt scanning with PII detection
   - SSN, Email, AWS Key, Passport detection
   - Result display (verdict, reason, latency, cache status)
   - Text highlighting for matched rules

4. **Audit Log**
   - Event recording for all scans
   - Filtering and pagination
   - Detailed view with rule matches
   - PII redaction verification

5. **Rules Editor**
   - YAML rule editing
   - Live validation
   - Rule testing functionality
   - Save and apply rules

6. **Cache Management**
   - Cache statistics display
   - Cache entries table
   - Cache flush functionality
   - Entry deletion

7. **Configuration**
   - LLM Provider settings
   - Cache configuration
   - Authentication settings
   - Scanner settings

8. **Complete Workflow**
   - End-to-end pipeline testing
   - Multiple prompt types
   - State persistence across pages

## Prerequisites

1. Node.js 18+ installed
2. Backend running on `http://localhost:8000`
3. Frontend running on `http://localhost:3000`

## Setup

### Windows
```bash
setup-e2e-tests.bat
```

### Linux/Mac
```bash
./setup-e2e-tests.sh
```

### Manual Setup
```bash
npm install -D @playwright/test
npx playwright install
```

## Running Tests

### Run all tests
```bash
npm run test:e2e
```

### Run tests with UI mode
```bash
npm run test:e2e:ui
```

### Run specific test file
```bash
npx playwright test tests/e2e/mvp-flow.spec.ts
```

### Run tests with specific browser
```bash
npx playwright test --headed --browser=chromium
```

### Run tests in CI mode
```bash
CI=true npm run test:e2e
```

## Test Data

Test data is stored in `fixtures/test-data.json`:
- API keys for testing
- Sample prompts (clean and with PII)
- Expected results and error messages
- Configuration values

## Configuration

Playwright configuration is in `playwright.config.ts`:
- Tests run in `/tests/e2e` directory
- Base URL: `http://localhost:3000`
- Web server automatically starts before tests
- Parallel execution enabled
- HTML reporter enabled
- Trace collection on retry

## Test Structure

Each test follows the MVP flow:

1. **Login** - Authenticate with API key
2. **Dashboard** - Verify main metrics
3. **Test Page** - Scan prompts and verify results
4. **Audit Log** - Check event recording
5. **Rules Editor** - Edit and test rules
6. **Cache Management** - Verify caching
7. **Configuration** - Modify settings
8. **Logout** - End session

## Debugging

### View test traces
```bash
npx playwright show-trace trace.zip
```

### Run tests in debug mode
```bash
npx playwright test --debug
```

### Run tests with screenshots on failure
```bash
npx playwright test --reporter=list
```

## Continuous Integration

The tests are designed to run in CI environments:
- No headed mode by default
- Retries enabled for CI
- Workers optimized for CI
- Minimal output for CI logs

## Browser Support

Tests run on:
- Chromium (Chrome/Edge)
- Firefox
- WebKit (Safari)
- Mobile viewports

## Maintenance

### Adding new tests
1. Create test file in `tests/e2e/`
2. Follow existing naming convention: `*.spec.ts`
3. Use test data from `fixtures/test-data.json`
4. Add selectors to `[data-testid=""]` attributes

### Updating selectors
- Update test selectors when UI changes
- Maintain consistency across tests
- Use meaningful test IDs

### Adding new test data
- Update `fixtures/test-data.json`
- Follow existing JSON structure
- Add expected results for new scenarios