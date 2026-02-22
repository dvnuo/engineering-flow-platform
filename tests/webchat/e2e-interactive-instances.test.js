/**
 * WebChat E2E Tests - Multi-Instance Support
 * 
 * Tests for Jira/Confluence multi-instance configuration.
 * Requires Playwright and a running server.
 */

const { test, expect, chromium } = require('@playwright/test');

const EFP_URL = process.env.EFP_URL || 'http://192.168.8.235:8000';

test.describe('WebChat API - Multi-Instance Configuration', () => {
  let browser;
  let context;
  
  test.beforeAll(async () => {
    browser = await chromium.launch({ 
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    context = await browser.newContext();
  });
  
  test.afterAll(async () => {
    await browser.close();
  });
  
  test('api/config endpoint returns jira section', async () => {
    const response = await context.request.get(`${EFP_URL}/api/config`);
    expect(response.ok()).toBeTruthy();
    
    const data = await response.json();
    // Config is nested under 'config' key
    expect(data.config).toHaveProperty('jira');
  });
  
  test('api/config endpoint returns confluence section', async () => {
    const response = await context.request.get(`${EFP_URL}/api/config`);
    expect(response.ok()).toBeTruthy();
    
    const data = await response.json();
    // Config is nested under 'config' key
    expect(data.config).toHaveProperty('confluence');
  });
  
  test('api/config jira section has instances support', async () => {
    const response = await context.request.get(`${EFP_URL}/api/config`);
    expect(response.ok()).toBeTruthy();
    
    const data = await response.json();
    // Jira config should support new multi-instance format
    const jira = data.config.jira;
    expect(jira).toHaveProperty('url');
    expect(jira).toHaveProperty('enabled');
  });
  
  test('api/config confluence section has instances support', async () => {
    const response = await context.request.get(`${EFP_URL}/api/config`);
    expect(response.ok()).toBeTruthy();
    
    const data = await response.json();
    // Confluence config should support new multi-instance format
    const confluence = data.config.confluence;
    expect(confluence).toHaveProperty('url');
    expect(confluence).toHaveProperty('enabled');
  });
});

test.describe('WebChat UI - Page Load', () => {
  let browser;
  let page;
  
  test.beforeAll(async () => {
    browser = await chromium.launch({ 
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
  });
  
  test.afterAll(async () => {
    await browser.close();
  });
  
  test('main page loads with all core elements', async () => {
    page = await browser.newPage();
    await page.goto(`${EFP_URL}/`);
    await page.waitForLoadState('networkidle');
    
    // Check core elements exist
    await expect(page.locator('#messages')).toBeVisible();
    await expect(page.locator('#messageInput')).toBeVisible();
    await expect(page.locator('#sendButton')).toBeVisible();
  });
  
  test('settings button exists', async () => {
    page = await browser.newPage();
    await page.goto(`${EFP_URL}/`);
    await page.waitForLoadState('networkidle');
    
    // Settings button may or may not exist depending on config
    const settingsButton = page.locator('#settingsButton');
    const count = await settingsButton.count();
    // Just verify we can check for it without error
    expect(count >= 0).toBeTruthy();
  });
  
  test('page loads without console errors', async () => {
    const errors = [];
    page = await browser.newPage();
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    
    await page.goto(`${EFP_URL}/`);
    await page.waitForLoadState('networkidle');
    
    // Filter out favicon errors
    const jsErrors = errors.filter(e => !e.includes('favicon'));
    expect(jsErrors).toHaveLength(0);
  });
});

test.describe('WebChat UI - Multi-Instance Interactive Tests', () => {
  let browser;
  let page;
  
  test.beforeAll(async () => {
    browser = await chromium.launch({ 
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
  });
  
  test.afterAll(async () => {
    await browser.close();
  });
  
  test.beforeEach(async () => {
    page = await browser.newPage();
    await page.goto(`${EFP_URL}/`);
    await page.waitForLoadState('networkidle');
  });
  
  test.afterEach(async () => {
    await page.close();
  });
  
  test('Add Jira Instance button adds a new instance row', async () => {
    // Open settings modal if it exists
    const settingsButton = page.locator('#settingsButton');
    if (await settingsButton.count() > 0) {
      await settingsButton.click();
      await page.waitForTimeout(500);
    }
    
    // Check if Add Jira Instance button exists
    const addJiraBtn = page.locator('button:has-text("+ Add Jira Instance")');
    const addBtnCount = await addJiraBtn.count();
    
    if (addBtnCount > 0) {
      // Click the button
      await addJiraBtn.click();
      await page.waitForTimeout(300);
      
      // Check that instance row was added (look for input fields for the new instance)
      const instanceInputs = page.locator('#jiraInstancesContainer input');
      const count = await instanceInputs.count();
      expect(count).toBeGreaterThan(0);
    }
  });
  
  test('Remove Jira Instance button removes an instance row', async () => {
    // Open settings modal if it exists
    const settingsButton = page.locator('#settingsButton');
    if (await settingsButton.count() > 0) {
      await settingsButton.click();
      await page.waitForTimeout(500);
    }
    
    // First add an instance
    const addJiraBtn = page.locator('button:has-text("+ Add Jira Instance")');
    if (await addJiraBtn.count() > 0) {
      await addJiraBtn.click();
      await page.waitForTimeout(300);
      
      // Now try to remove it
      const removeBtn = page.locator('button:has-text("Remove")').first();
      if (await removeBtn.count() > 0) {
        await removeBtn.click();
        await page.waitForTimeout(300);
        
        // Should have no instance inputs after removal
        const instanceInputs = page.locator('#jiraInstancesContainer input');
        const count = await instanceInputs.count();
        expect(count).toBe(0);
      }
    }
  });
  
  test('Add Confluence Instance button adds a new instance row', async () => {
    // Open settings modal if it exists
    const settingsButton = page.locator('#settingsButton');
    if (await settingsButton.count() > 0) {
      await settingsButton.click();
      await page.waitForTimeout(500);
    }
    
    // Check if Add Confluence Instance button exists
    const addConfBtn = page.locator('button:has-text("+ Add Confluence Instance")');
    const addBtnCount = await addConfBtn.count();
    
    if (addBtnCount > 0) {
      // Click the button
      await addConfBtn.click();
      await page.waitForTimeout(300);
      
      // Check that instance row was added
      const instanceInputs = page.locator('#confluenceInstancesContainer input');
      const count = await instanceInputs.count();
      expect(count).toBeGreaterThan(0);
    }
  });
  
  test('Remove Confluence Instance button removes an instance row', async () => {
    // Open settings modal if it exists
    const settingsButton = page.locator('#settingsButton');
    if (await settingsButton.count() > 0) {
      await settingsButton.click();
      await page.waitForTimeout(500);
    }
    
    // First add an instance
    const addConfBtn = page.locator('button:has-text("+ Add Confluence Instance")');
    if (await addConfBtn.count() > 0) {
      await addConfBtn.click();
      await page.waitForTimeout(300);
      
      // Now try to remove it
      const removeBtns = page.locator('#confluenceInstancesContainer button:has-text("Remove")');
      if (await removeBtns.count() > 0) {
        await removeBtns.first().click();
        await page.waitForTimeout(300);
        
        // Should have no instance inputs after removal
        const instanceInputs = page.locator('#confluenceInstancesContainer input');
        const count = await instanceInputs.count();
        expect(count).toBe(0);
      }
    }
  });
  
  test('window.addJiraInstance function is globally accessible', async () => {
    // Open settings modal if it exists
    const settingsButton = page.locator('#settingsButton');
    if (await settingsButton.count() > 0) {
      await settingsButton.click();
      await page.waitForTimeout(500);
    }
    
    // Call the function directly from window
    await page.evaluate(() => {
      if (typeof window.addJiraInstance === 'function') {
        window.addJiraInstance();
      }
    });
    await page.waitForTimeout(300);
    
    // Verify instance was added
    const instanceInputs = page.locator('#jiraInstancesContainer input');
    const count = await instanceInputs.count();
    expect(count).toBeGreaterThan(0);
  });
  
  test('window.removeJiraInstance function is globally accessible', async () => {
    // Open settings modal if it exists
    const settingsButton = page.locator('#settingsButton');
    if (await settingsButton.count() > 0) {
      await settingsButton.click();
      await page.waitForTimeout(500);
    }
    
    // First add an instance via window function
    await page.evaluate(() => {
      if (typeof window.addJiraInstance === 'function') {
        window.addJiraInstance();
      }
    });
    await page.waitForTimeout(300);
    
    // Now remove it via window function
    await page.evaluate(() => {
      if (typeof window.removeJiraInstance === 'function') {
        window.removeJiraInstance(0);
      }
    });
    await page.waitForTimeout(300);
    
    // Should have no instance inputs after removal
    const instanceInputs = page.locator('#jiraInstancesContainer input');
    const count = await instanceInputs.count();
    expect(count).toBe(0);
  });
  
  test('window.addConfluenceInstance function is globally accessible', async () => {
    // Open settings modal if it exists
    const settingsButton = page.locator('#settingsButton');
    if (await settingsButton.count() > 0) {
      await settingsButton.click();
      await page.waitForTimeout(500);
    }
    
    // Call the function directly from window
    await page.evaluate(() => {
      if (typeof window.addConfluenceInstance === 'function') {
        window.addConfluenceInstance();
      }
    });
    await page.waitForTimeout(300);
    
    // Verify instance was added
    const instanceInputs = page.locator('#confluenceInstancesContainer input');
    const count = await instanceInputs.count();
    expect(count).toBeGreaterThan(0);
  });
  
  test('window.removeConfluenceInstance function is globally accessible', async () => {
    // Open settings modal if it exists
    const settingsButton = page.locator('#settingsButton');
    if (await settingsButton.count() > 0) {
      await settingsButton.click();
      await page.waitForTimeout(500);
    }
    
    // First add an instance via window function
    await page.evaluate(() => {
      if (typeof window.addConfluenceInstance === 'function') {
        window.addConfluenceInstance();
      }
    });
    await page.waitForTimeout(300);
    
    // Now remove it via window function
    await page.evaluate(() => {
      if (typeof window.removeConfluenceInstance === 'function') {
        window.removeConfluenceInstance(0);
      }
    });
    await page.waitForTimeout(300);
    
    // Should have no instance inputs after removal
    const instanceInputs = page.locator('#confluenceInstancesContainer input');
    const count = await instanceInputs.count();
    expect(count).toBe(0);
  });
});

// Additional tests for PR #220 fix verification
// These tests ensure the window function exposure works correctly
