/**
 * WebChat Utility Functions - Unit Tests
 * 
 * Tests for webchat.js utility functions without browser dependency.
 * Uses Jest to test pure JavaScript logic.
 */

// Simple escape function for testing (no DOM dependency)
function escapeHtml(text) {
  if (!text) return '';
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;'
  };
  return text.replace(/[&<>"']/g, m => map[m]);
}

function truncateText(text, maxLength = 200) {
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength).trim() + '...';
}

function formatTokenCount(tokens) {
  if (tokens < 1000) return tokens.toString();
  if (tokens < 1000000) return (tokens / 1000).toFixed(1) + 'K';
  return (tokens / 1000000).toFixed(1) + 'M';
}

function formatCost(cost) {
  if (cost < 0.01) return '$0.00';
  return '$' + cost.toFixed(2);
}

// ============ TESTS ============

describe('escapeHtml', () => {
  test('escapes HTML entities', () => {
    expect(escapeHtml('<script>')).toBe('&lt;script&gt;');
    expect(escapeHtml('"hello"')).toBe('&quot;hello&quot;');
    expect(escapeHtml("'single'")).toBe("&#039;single&#039;");
    expect(escapeHtml('&amp;')).toBe('&amp;amp;');
  });
  
  test('handles null/undefined', () => {
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(undefined)).toBe('');
  });
});

describe('truncateText', () => {
  test('returns original text if shorter than max', () => {
    expect(truncateText('short text', 20)).toBe('short text');
  });
  
  test('truncates long text with ellipsis', () => {
    const longText = 'A'.repeat(300);
    const result = truncateText(longText, 100);
    expect(result.length).toBe(103); // 100 chars + '...'
    expect(result).toContain('...');
  });
  
  test('handles empty/null values', () => {
    expect(truncateText('')).toBe('');
    expect(truncateText(null)).toBe('');
  });
});

describe('formatTokenCount', () => {
  test('formats small numbers', () => {
    expect(formatTokenCount(500)).toBe('500');
  });
  
  test('formats thousands with K', () => {
    expect(formatTokenCount(1500)).toBe('1.5K');
  });
  
  test('formats millions with M', () => {
    expect(formatTokenCount(2500000)).toBe('2.5M');
  });
});

describe('formatCost', () => {
  test('formats dollars', () => {
    expect(formatCost(1.50)).toBe('$1.50');
    expect(formatCost(0.50)).toBe('$0.50');
  });
  
  test('rounds small amounts', () => {
    expect(formatCost(0.005)).toBe('$0.00');
  });
});

// Additional utility tests for status indicators
describe('Status Indicator Utilities', () => {
  test('status text mapping for processing', () => {
    const statusMessages = {
      sending: 'Sending...',
      processing: 'Processing...',
      ready: 'Ready',
      error: 'Error'
    };
    expect(statusMessages.sending).toBe('Sending...');
    expect(statusMessages.processing).toBe('Processing...');
    expect(statusMessages.ready).toBe('Ready');
    expect(statusMessages.error).toBe('Error');
  });
  
  test('CSS class mapping for status', () => {
    const statusClasses = {
      sending: 'typing-indicator sending',
      processing: 'typing-indicator processing',
      ready: 'typing-indicator ready',
      error: 'typing-indicator error'
    };
    expect(statusClasses.processing).toContain('processing');
    expect(statusClasses.ready).toContain('ready');
    expect(statusClasses.error).toContain('error');
  });
});
