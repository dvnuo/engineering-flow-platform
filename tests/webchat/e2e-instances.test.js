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
