#!/bin/bash

# Setup script for E2E tests
# This script installs Playwright and browsers for testing

echo "Setting up E2E tests..."

# Install Playwright
npm install -D @playwright/test

# Install Playwright browsers
npx playwright install

echo "E2E tests setup complete!"
echo "To run tests: npm run test:e2e"
echo "To run tests with UI: npm run test:e2e:ui"