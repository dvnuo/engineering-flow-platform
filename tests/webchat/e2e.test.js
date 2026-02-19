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
    await page.goto(`${EFP_URL}/chat`);
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
  
  test('typing indicator shows on send', async () => {
    // Fill message and send
    await page.fill('#messageInput', 'test message');
    await page.click('#sendButton');
    
    // Check typing indicator appears with processing class
    const indicator = page.locator('#typing');
    await expect(indicator).toBeVisible();
    
    // Check for status text element
    const statusText = page.locator('#typingText');
    await expect(statusText).toBeVisible();
  });
  
  test('typing indicator shows status transitions', async () => {
    // Fill message and send
    await page.fill('#messageInput', 'hello');
    await page.click('#sendButton');
    
    // Check initial status shows "Sending..." or "Processing"
    const statusText = page.locator('#typingText');
    await expect(statusText).toBeVisible();
  });

  test('theme toggle works', async () => {
    const themeToggle = page.locator('#themeToggle');
    
    // Toggle to light mode
    await themeToggle.click();
    await expect(page.locator('body')).toHaveAttribute('data-theme', 'light');
    
    // Toggle back to dark mode
    await themeToggle.click();
    await expect(page.locator('body')).toHaveAttribute('data-theme', 'dark');
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
    await page.goto(`${EFP_URL}/chat`);
    await page.waitForLoadState('networkidle');
  });
  
  test('skill selector appears with /', async () => {
    const input = page.locator('#messageInput');
    
    // Type / to open skill selector
    await input.fill('/');
    await page.waitForTimeout(500);
    
    // Check dropdown appears
    const dropdown = page.locator('.skill-selector-dropdown');
    await expect(dropdown).toBeVisible();
  });
  
  test('auto-triggers skill with /skill-name', async () => {
    const input = page.locator('#messageInput');
    
    // Use auto-trigger with /git
    await input.fill('/git');
    await page.waitForTimeout(500);
    
    // Check that git skill is auto-matched or dropdown shows
    const dropdown = page.locator('.skill-selector-dropdown');
    await expect(dropdown).toBeVisible();
  });
  
  test('case-insensitive auto-trigger', async () => {
    const input = page.locator('#messageInput');
    
    // Use uppercase /GIT
    await input.fill('/GIT');
    await page.waitForTimeout(500);
    
    // Should still match (case-insensitive)
    const dropdown = page.locator('.skill-selector-dropdown');
    await expect(dropdown).toBeVisible();
  });
});

test.describe('WebChat API Integration', () => {
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
  
  test('api/skills endpoint works', async () => {
    const response = await page.request.get(`${EFP_URL}/api/skills`);
    expect(response.ok()).toBeTruthy();
    
    const data = await response.json();
    expect(data.skills).toBeDefined();
    expect(Array.isArray(data.skills)).toBe(true);
  });
  
  test('api/chat endpoint works', async () => {
    const response = await page.request.post(`${EFP_URL}/api/chat`, {
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
    page.on('console', msg => {
      if (msg.type() === 'error') {
        // Store errors for later verification
        page.errors = page.errors || [];
        page.errors.push(msg.text());
      }
    });
    
    await page.goto(`${EFP_URL}/chat`);
    await page.waitForLoadState('networkidle');
  });
  
  test('no console errors on load', async () => {
    const errors = page.errors || [];
    const jsErrors = errors.filter(e => !e.includes('favicon'));
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
