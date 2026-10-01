import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

// Load test data
const testData = JSON.parse(
  readFileSync(join(__dirname, 'fixtures/test-data.json'), 'utf-8')
);

test.describe('MVP Pipeline E2E Test', () => {
  let page: any;

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    await page.goto('/');
  });

  test('1. Login flow redirects to dashboard', async () => {
    // Should redirect to login page
    await expect(page).toHaveURL('/login');
    
    // Fill login form
    await page.fill('input[name="apiKey"]', testData.testUser.apiKey);
    await page.click('button:has-text("Login")');
    
    // Should redirect to dashboard
    await expect(page).toHaveURL('/dashboard');
  });

  test('2. Dashboard shows KPI cards and recent activity', async () => {
    // Wait for dashboard to load
    await page.waitForSelector('[data-testid="kpi-cards"]');
    
    // Check KPI cards are present
    const kpiCards = page.locator('[data-testid="kpi-card"]');
    await expect(kpiCards.first()).toBeVisible();
    await expect(kpiCards.nth(1)).toBeVisible();
    await expect(kpiCards.nth(2)).toBeVisible();
    await expect(kpiCards.nth(3)).toBeVisible();
    
    // Check recent activity table
    await expect(page.locator('[data-testid="recent-activity"]')).toBeVisible();
  });

  test('3. Test page with PII detection - SSN', async () => {
    // Navigate to test page
    await page.click('nav:has-text("Test")');
    await expect(page).toHaveURL('/test');
    
    // Fill test prompt with SSN
    await page.fill('textarea[name="prompt"]', testData.testPrompts.piiSsn);
    await page.click('button:has-text("Scan")');
    
    // Wait for result
    await page.waitForSelector('[data-testid="scan-result"]');
    
    // Check verdict is blocked
    const verdictBadge = page.locator('[data-testid="verdict-badge"]');
    await expect(verdictBadge).toBeVisible();
    await expect(verdictBadge).toHaveText('Block');
    
    // Check reason contains PII detection
    const reasonText = page.locator('[data-testid="reason-text"]');
    await expect(reasonText).toBeVisible();
    await expect(reasonText).toContainText(testData.expectedResults.blockReasons.ssn);
    
    // Check latency is displayed
    const latency = page.locator('[data-testid="latency-ms"]');
    await expect(latency).toBeVisible();
    
    // Check cache status
    const cacheStatus = page.locator('[data-testid="cache-status"]');
    await expect(cacheStatus).toBeVisible();
    
    // Check request ID is present
    const requestId = page.locator('[data-testid="request-id"]');
    await expect(requestId).toBeVisible();
  });

  test('4. Audit log shows scanned event', async () => {
    // Navigate to audit page
    await page.click('nav:has-text("Audit")');
    await expect(page).toHaveURL('/audit');
    
    // Wait for audit table to load
    await page.waitForSelector('[data-testid="audit-table"]');
    
    // Check that audit event is present
    const auditRow = page.locator('tr:has-text("Block")').first();
    await expect(auditRow).toBeVisible();
    
    // Verify the event contains expected data
    await expect(auditRow).toContainText(testData.expectedResults.blockReasons.ssn);
    
    // Click on row to expand details
    await auditRow.click();
    
    // Check expanded details are shown
    const expandedDetails = page.locator('[data-testid="audit-details"]');
    await expect(expandedDetails).toBeVisible();
  });

  test('5. Rules editor can edit and save rules', async () => {
    // Navigate to rules page
    await page.click('nav:has-text("Rules")');
    await expect(page).toHaveURL('/rules');
    
    // Wait for rules list to load
    await page.waitForSelector('[data-testid="rules-list"]');
    
    // Select a rule to edit (e.g., SSN rule)
    await page.click('text="US Social Security Number"');
    
    // Wait for editor to load
    await page.waitForSelector('[data-testid="rule-editor"]');
    
    // Modify the rule pattern
    const patternInput = page.locator('input[name="pattern"]');
    await patternInput.clear();
    await patternInput.fill('\\b\\d{3}-\\d{2}-\\d{4}\\b');
    
    // Save the rule
    await page.click('button:has-text("Save")');
    
    // Wait for save confirmation
    await expect(page.locator('[data-testid="save-success"]')).toBeVisible();
    
    // Test the rule with a sample prompt
    await page.click('button:has-text("Test")');
    
    // Test modal should appear
    await page.waitForSelector('[data-testid="test-modal"]');
    
    // Fill test prompt
    await page.fill('textarea[name="testPrompt"]', testData.testPrompts.piiSsn);
    await page.click('button:has-text("Run Test")');
    
    // Check test results
    await expect(page.locator('[data-testid="test-results"]')).toBeVisible();
    await expect(page.locator('[data-testid="test-results"]')).toContainText('Match found');
  });

  test('6. Cache management shows cached entries', async () => {
    // Navigate to cache page
    await page.click('nav:has-text("Cache")');
    await expect(page).toHaveURL('/cache');
    
    // Wait for cache stats to load
    await page.waitForSelector('[data-testid="cache-stats"]');
    
    // Check cache stats are displayed
    const statsCards = page.locator('[data-testid="stat-card"]');
    await expect(statsCards.first()).toBeVisible();
    await expect(statsCards.nth(1)).toBeVisible();
    await expect(statsCards.nth(2)).toBeVisible();
    await expect(statsCards.nth(3)).toBeVisible();
    
    // Check cache entries table
    await expect(page.locator('[data-testid="cache-entries"]')).toBeVisible();
    
    // Verify block entry is present
    const blockEntry = page.locator('tr:has-text("Block")').first();
    await expect(blockEntry).toBeVisible();
  });

  test('7. Configuration page can modify settings', async () => {
    // Navigate to config page
    await page.click('nav:has-text("Config")');
    await expect(page).toHaveURL('/config');
    
    // Wait for config sections to load
    await page.waitForSelector('[data-testid="config-sections"]');
    
    // Modify LLM Provider settings
    await page.click('text="LLM Provider"');
    
    const urlInput = page.locator('input[name="providerUrl"]');
    await urlInput.clear();
    await urlInput.fill('http://localhost:8001');
    
    const timeoutInput = page.locator('input[name="timeout"]');
    await timeoutInput.clear();
    await timeoutInput.fill('30');
    
    // Modify cache settings
    await page.click('text="Cache"');
    
    const ttlInput = page.locator('input[name="cacheTtl"]');
    await ttlInput.clear();
    await ttlInput.fill(testData.configValues.cacheTtl);
    
    // Save configuration
    await page.click('button:has-text("Save")');
    
    // Wait for save confirmation
    await expect(page.locator('[data-testid="save-success"]')).toBeVisible();
  });

  test('8. Logout functionality works', async () => {
    // Click logout button
    await page.click('button:has-text("Logout")');
    
    // Should redirect to login page
    await expect(page).toHaveURL('/login');
    
    // Verify login form is visible
    await expect(page.locator('input[name="apiKey"]')).toBeVisible();
  });

  test('9. Complete flow with clean prompt', async () => {
    // Login
    await page.fill('input[name="apiKey"]', testData.testUser.apiKey);
    await page.click('button:has-text("Login")');
    await expect(page).toHaveURL('/dashboard');
    
    // Navigate to test page
    await page.click('nav:has-text("Test")');
    await expect(page).toHaveURL('/test');
    
    // Test with clean prompt
    await page.fill('textarea[name="prompt"]', testData.testPrompts.clean);
    await page.click('button:has-text("Scan")');
    
    // Wait for result
    await page.waitForSelector('[data-testid="scan-result"]');
    
    // Check verdict is allowed
    const verdictBadge = page.locator('[data-testid="verdict-badge"]');
    await expect(verdictBadge).toHaveText('Allow');
    
    // Check recent activity shows the clean prompt
    await page.click('nav:has-text("Audit")');
    await expect(page).toHaveURL('/audit');
    
    const cleanAuditEntry = page.locator('tr:has-text("Allow")').first();
    await expect(cleanAuditEntry).toBeVisible();
  });

  test('10. Error handling - invalid API key', async () => {
    // Navigate to login page
    await page.goto('/login');
    
    // Try to login with invalid API key
    await page.fill('input[name="apiKey"]', testData.testUser.invalidApiKey);
    await page.click('button:has-text("Login")');
    
    // Should show error message
    await expect(page.locator('[data-testid="error-message"]')).toBeVisible();
    await expect(page.locator('[data-testid="error-message"]')).toContainText('Invalid API key');
  });
});