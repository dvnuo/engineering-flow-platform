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
    // Clear any existing messages
    await page.fill('#messageInput', 'test message');
    
    // Send message
    await page.click('#sendButton');
    
    // Check typing indicator appears
    const indicator = page.locator('#typing');
    await expect(indicator).toBeVisible();
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
  
  test('displays available skills', async () => {
    const input = page.locator('#messageInput');
    
    // Type / to open skill selector
    await input.fill('/');
    await page.waitForTimeout(500);
    
    // Check skills are displayed
    const skillItems = page.locator('.skill-item');
    const count = await skillItems.count();
    expect(count).toBeGreaterThan(0);
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
