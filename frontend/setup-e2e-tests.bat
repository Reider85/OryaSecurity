@echo off
echo Setting up E2E tests...

REM Install Playwright
npm install -D @playwright/test

REM Install Playwright browsers
npx playwright install

echo E2E tests setup complete!
echo To run tests: npm run test:e2e
echo To run tests with UI: npm run test:e2e:ui

pause