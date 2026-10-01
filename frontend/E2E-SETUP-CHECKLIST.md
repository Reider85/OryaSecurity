# E2E Test Setup Checklist

## ✅ Files Created

### Configuration Files
- [x] `playwright.config.ts` - Playwright configuration
- [x] `package.json` - Updated with test scripts

### Test Files
- [x] `tests/e2e/mvp-flow.spec.ts` - Main E2E test file
- [x] `tests/e2e/README.md` - Test documentation
- [x] `tests/e2e/fixtures/test-data.json` - Test data

### Setup Scripts
- [x] `setup-e2e-tests.sh` - Linux/Mac setup script
- [x] `setup-e2e-tests.bat` - Windows setup script
- [x] `verify-e2e-setup.js` - Verification script (requires Node.js)

## 📋 Test Coverage Verification

### Authentication Tests
- [x] Login with valid API key
- [x] Error handling for invalid API key
- [x] Logout functionality

### Dashboard Tests
- [x] KPI cards display
- [x] Recent activity table
- [x] Live metrics updates

### Test Page Tests
- [x] SSN detection and blocking
- [x] Email detection and blocking
- [x] AWS key detection and blocking
- [x] Passport detection and blocking
- [x] Clean prompt allowing
- [x] Result display components

### Audit Log Tests
- [x] Event recording verification
- [x] Table display with pagination
- [x] Detailed view expansion
- [x] PII redaction verification

### Rules Editor Tests
- [x] Rule selection and editing
- [x] YAML validation
- [x] Rule testing functionality
- [x] Save and apply rules

### Cache Management Tests
- [x] Cache statistics display
- [x] Cache entries table
- [x] Cache flush functionality
- [x] Entry deletion

### Configuration Tests
- [x] LLM Provider settings
- [x] Cache configuration
- [x] Authentication settings
- [x] Scanner settings

### Complete Workflow Tests
- [x] End-to-end pipeline
- [x] Multiple prompt types
- [x] State persistence
- [x] Error handling

## 🔧 Setup Instructions

### Prerequisites
- [ ] Node.js 18+ installed
- [ ] Backend running on `http://localhost:8000`
- [ ] Frontend running on `http://localhost:3000`

### Installation Steps
1. [ ] Install Playwright dependencies:
   ```bash
   npm install -D @playwright/test
   ```

2. [ ] Install Playwright browsers:
   ```bash
   npx playwright install
   ```

3. [ ] Verify setup:
   ```bash
   node verify-e2e-setup.js
   ```

### Running Tests
1. [ ] Run all tests:
   ```bash
   npm run test:e2e
   ```

2. [ ] Run tests with UI:
   ```bash
   npm run test:e2e:ui
   ```

3. [ ] Run specific test:
   ```bash
   npx playwright test tests/e2e/mvp-flow.spec.ts
   ```

## 🧪 Test Data Verification

### test-data.json Structure
- [x] `testUser` - API keys for testing
- [x] `testPrompts` - Sample prompts (clean and PII)
- [x] `expectedResults` - Expected block reasons
- [x] `configValues` - Configuration values
- [x] `ruleExamples` - Sample rules

### Test Scenarios
- [x] Clean prompt → Allow
- [x] SSN prompt → Block with reason
- [x] Email prompt → Block with reason
- [x] AWS key prompt → Block with reason
- [x] Invalid login → Error message
- [x] Rule editing → Save and test
- [x] Cache operations → Flush and delete

## 🚀 Next Steps

1. **Setup Environment**
   - Install Node.js if not available
   - Run setup scripts
   - Install dependencies

2. **Verify Backend**
   - Ensure backend is running on port 8000
   - Verify API endpoints are accessible
   - Check database connections

3. **Run Tests**
   - Start frontend development server
   - Run E2E tests
   - Review test results

4. **Maintenance**
   - Update test data when UI changes
   - Add new test scenarios
   - Update selectors when needed

## 🔍 Troubleshooting

### Common Issues
- [ ] "npm not found" - Install Node.js
- [ ] "playwright not found" - Run `npm install -D @playwright/test`
- [ ] "browsers not found" - Run `npx playwright install`
- [ ] "connection refused" - Ensure backend is running
- [ ] "element not found" - Update test selectors

### Debug Commands
```bash
# Run tests in headed mode
npx playwright test --headed

# Run specific test with debugging
npx playwright test --debug

# Show test traces
npx playwright show-trace trace.zip

# Run tests with verbose output
npx playwright test --reporter=list
```