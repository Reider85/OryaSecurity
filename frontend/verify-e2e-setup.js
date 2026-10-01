const fs = require('fs');
const path = require('path');

console.log('🔍 Verifying E2E test setup...\n');

// Check if required files exist
const requiredFiles = [
  'playwright.config.ts',
  'tests/e2e/mvp-flow.spec.ts',
  'tests/e2e/fixtures/test-data.json',
  'tests/e2e/README.md'
];

console.log('📁 Checking required files:');
let allFilesExist = true;

requiredFiles.forEach(file => {
  const filePath = path.join(__dirname, file);
  if (fs.existsSync(filePath)) {
    console.log(`  ✅ ${file}`);
  } else {
    console.log(`  ❌ ${file} - MISSING`);
    allFilesExist = false;
  }
});

// Check package.json for test scripts
const packageJsonPath = path.join(__dirname, 'package.json');
if (fs.existsSync(packageJsonPath)) {
  const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf-8'));
  const testScripts = packageJson.scripts || {};
  
  console.log('\n📦 Checking test scripts:');
  if (testScripts['test:e2e']) {
    console.log('  ✅ test:e2e script found');
  } else {
    console.log('  ❌ test:e2e script missing');
    allFilesExist = false;
  }
  
  if (testScripts['test:e2e:ui']) {
    console.log('  ✅ test:e2e:ui script found');
  } else {
    console.log('  ❌ test:e2e:ui script missing');
    allFilesExist = false;
  }
}

// Check test data structure
const testDataPath = path.join(__dirname, 'tests/e2e/fixtures/test-data.json');
if (fs.existsSync(testDataPath)) {
  const testData = JSON.parse(fs.readFileSync(testDataPath, 'utf-8'));
  
  console.log('\n🧪 Checking test data structure:');
  const requiredData = ['testUser', 'testPrompts', 'expectedResults', 'configValues', 'ruleExamples'];
  
  requiredData.forEach(field => {
    if (testData[field]) {
      console.log(`  ✅ ${field}`);
    } else {
      console.log(`  ❌ ${field} - MISSING`);
      allFilesExist = false;
    }
  });
}

console.log('\n' + '='.repeat(50));
if (allFilesExist) {
  console.log('🎉 All E2E test setup files are in place!');
  console.log('\n🚀 Next steps:');
  console.log('1. Run setup: setup-e2e-tests.bat (Windows) or ./setup-e2e-tests.sh (Linux/Mac)');
  console.log('2. Install dependencies: npm install -D @playwright/test');
  console.log('3. Install browsers: npx playwright install');
  console.log('4. Run tests: npm run test:e2e');
} else {
  console.log('❌ Some files are missing. Please check the setup.');
}

console.log('\n📋 Manual verification checklist:');
console.log('[] Playwright config exists and is valid');
console.log('[] E2E test file exists and has test cases');
console.log('[] Test data fixtures are complete');
console.log('[] Package.json has test scripts');
console.log('[] Setup scripts are available');
console.log('[] README documentation is present');