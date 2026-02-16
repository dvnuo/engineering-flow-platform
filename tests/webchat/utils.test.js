/**
 * WebChat Utility Functions - Unit Tests
 * 
 * Tests for webchat.js utility functions without browser dependency.
 * Uses Jest to test pure JavaScript logic.
 */

// Mock DOM environment for testing
const mockDocument = {
  getElementById: jest.fn(),
  querySelector: jest.fn(),
  querySelectorAll: jest.fn()
};

global.document = mockDocument;

// Utility functions extracted for testing
function formatSmartDate(dateStr) {
  const now = new Date();
  const messageDate = new Date(dateStr);
  const timeStr = messageDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  
  // Same day
  if (messageDate.toDateString() === now.toDateString()) {
    return timeStr;
  }
  
  // Yesterday
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (messageDate.toDateString() === yesterday.toDateString()) {
    return `Yesterday ${timeStr}`;
  }
  
  // Within the last 7 days
  const oneWeekAgo = new Date(now);
  oneWeekAgo.setDate(oneWeekAgo.getDate() - 7);
  if (messageDate > oneWeekAgo) {
    const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    return `${days[messageDate.getDay()]} ${timeStr}`;
  }
  
  // Within the same year
  if (messageDate.getFullYear() === now.getFullYear()) {
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const month = months[messageDate.getMonth()];
    const day = messageDate.getDate();
    return `${month} ${day} ${timeStr}`;
  }
  
  // Over a year ago
  const year = messageDate.getFullYear();
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const month = months[messageDate.getMonth()];
  return `${month} ${day} ${year}`;
}

function escapeHtml(text) {
  const div = global.document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
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

describe('formatSmartDate', () => {
  test('formats time for same day', () => {
    const now = new Date();
    const result = formatSmartDate(now.toISOString());
    // Should be just time like "14:30"
    expect(result).toMatch(/^\d{2}:\d{2}$/);
  });
  
  test('formats yesterday correctly', () => {
    const yesterday = new Date(Date.now() - 86400000);
    const result = formatSmartDate(yesterday.toISOString());
    expect(result).toContain('Yesterday');
  });
  
  test('formats day name for within week', () => {
    const threeDaysAgo = new Date(Date.now() - 3 * 86400000);
    const result = formatSmartDate(threeDaysAgo.toISOString());
    const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    const expectedDay = days[threeDaysAgo.getDay()];
    expect(result).toContain(expectedDay);
  });
  
  test('formats month day for same year', () => {
    const lastMonth = new Date();
    lastMonth.setMonth(lastMonth.getMonth() - 1);
    const result = formatSmartDate(lastMonth.toISOString());
    expect(result).toMatch(/^[A-Za-z]+ \d{1,2} \d{2}:\d{2}$/);
  });
  
  test('handles invalid date gracefully', () => {
    const result = formatSmartDate('invalid-date');
    expect(result).toBe('NaN:NaN');
  });
});

describe('escapeHtml', () => {
  test('escapes HTML entities', () => {
    expect(escapeHtml('<script>')).toBe('&lt;script&gt;');
    expect(escapeHtml('"hello"')).toBe('&quot;hello&quot;');
    expect(escapeHtml("'single'")).toBe("&#x27;single&#x27;");
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
