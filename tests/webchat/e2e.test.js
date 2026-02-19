/**
 * WebChat E2E Tests - Playwright
 * 
 * End-to-end tests for WebChat UI.
 * Requires Playwright and a running server.
 * 
 * Usage:
 *   npm install
 *   npx playwright install chromium
 *   npm test -- --testPathPattern=e2e
 */

const { test, expect, chromium } = require('@playwright/test');

const EFP_URL = process.env.EFP_URL || 'http://192.168.8.235:8000';

test.describe('WebChat UI', () => {
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
  
  test('loads successfully', async () => {
    // Check page title
    await expect(page).toHaveTitle(/Engineering Flow/);
    
    // Check main elements exist
    await expect(page.locator('#messages')).toBeVisible();
    await expect(page.locator('#messageInput')).toBeVisible();
    await expect(page.locator('#sendButton')).toBeVisible();
  });
  
  test('typing indicator has .show class when sending', async () => {
    // Fill message and send
    await page.fill('#messageInput', 'test message');
    await page.click('#sendButton');
    
    // Check typing indicator gets .show class
    const indicator = page.locator('#typing');
    await expect(indicator).toHaveClass(/show/);
  });
  
  test('theme toggle works', async () => {
    const themeToggle = page.locator('#themeToggle');
    
    // Toggle to dark mode (default is light)
    await themeToggle.click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    
    // Toggle back to light mode
    await themeToggle.click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });
  
  test('sends message and receives response', async () => {
    const input = page.locator('#messageInput');
    const sendButton = page.locator('#sendButton');
    
    // Send a simple message
    await input.fill('hello');
    await sendButton.click();
    
    // Wait for response
    await page.waitForTimeout(5000);
    
    // Check that a response was received
    const messages = page.locator('.message');
    const messageCount = await messages.count();
    expect(messageCount).toBeGreaterThanOrEqual(2);
  });
});

test.describe('WebChat Skills', () => {
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
  
  test('skill selector has .active class with /', async () => {
    const input = page.locator('#messageInput');
    
    // Type / to open skill selector
    await input.fill('/');
    await page.waitForTimeout(500);
    
    // Check parent skillSelector has .active class
    const skillSelector = page.locator('#skillSelector');
    await expect(skillSelector).toHaveClass(/active/);
  });
  
  test('auto-triggers skill with /skill-name', async () => {
    const input = page.locator('#messageInput');
    
    // Use auto-trigger with /git
    await input.fill('/git');
    await page.waitForTimeout(500);
    
    // Check that skill selector has .active class
    const skillSelector = page.locator('#skillSelector');
    await expect(skillSelector).toHaveClass(/active/);
  });
  
  test('case-insensitive auto-trigger', async () => {
    const input = page.locator('#messageInput');
    
    // Use uppercase /GIT
    await input.fill('/GIT');
    await page.waitForTimeout(500);
    
    // Should still match (case-insensitive)
    const skillSelector = page.locator('#skillSelector');
    await expect(skillSelector).toHaveClass(/active/);
  });
});

test.describe('WebChat API Integration', () => {
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
  
  test('api/skills endpoint works', async () => {
    const response = await context.request.get(`${EFP_URL}/api/skills`);
    expect(response.ok()).toBeTruthy();
    
    const data = await response.json();
    expect(data.skills).toBeDefined();
    expect(Array.isArray(data.skills)).toBe(true);
  });
  
  test('api/chat endpoint works', async () => {
    const response = await context.request.post(`${EFP_URL}/api/chat`, {
      data: {
        message: 'test',
        session_id: 'e2e-test'
      }
    });
    expect(response.ok()).toBeTruthy();
    
    const data = await response.json();
    expect(data.response).toBeDefined();
    expect(data.session_id).toBeDefined();
  });
});

test.describe('CSS Validation', () => {
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
    
    // Collect console errors
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    
    await page.goto(`${EFP_URL}/`);
    await page.waitForLoadState('networkidle');
    page.errors = errors;
  });
  
  test('no console errors on load', async () => {
    const jsErrors = page.errors.filter(e => !e.includes('favicon'));
    expect(jsErrors).toHaveLength(0);
  });
  
  test('typing indicator has correct CSS classes', async () => {
    const indicator = page.locator('#typing');
    await expect(indicator).toHaveClass(/typing-indicator/);
  });
  
  test('status bar displays correctly', async () => {
    const statusBar = page.locator('.status-bar');
    await expect(statusBar).toBeVisible();
    
    const statusText = page.locator('#status');
    await expect(statusText).toBeVisible();
  });
});
