/**
 * Security utilities for XSS and CSRF protection
 */

/**
 * Get CSRF token from meta tag or generate a development token
 * In production, the server should inject a real CSRF token
 */
export function getCsrfToken() {
  const metaTag = document.querySelector('meta[name="csrf-token"]');
  const token = metaTag?.getAttribute('content');
  
  // In production, token should be injected by server
  // For development, use a placeholder that won't be '${CSRF_TOKEN}'
  if (!token || token === '${CSRF_TOKEN}') {
    // Development mode - check multiple conditions for better reliability
    // Vite uses import.meta.env, webpack uses process.env, also check hostname
    const isDevelopment = 
      (typeof import !== 'undefined' && import.meta?.env?.MODE === 'development') ||
      (typeof process !== 'undefined' && process.env?.NODE_ENV === 'development') ||
      window.location.hostname === 'localhost' ||
      window.location.hostname === '127.0.0.1';
    
    if (isDevelopment) {
      return 'dev_csrf_' + Math.random().toString(36).slice(2, 11);
    }
    
    // In production, missing CSRF token is a security issue
    console.error('CSRF token not found. Server must inject token in production.');
    return null;
  }
  
  return token;
}

/**
 * Escape text content to prevent XSS attacks
 * WARNING: This only escapes text. Do NOT use the output with innerHTML.
 * For HTML sanitization, use a library like DOMPurify.
 * @param {string} text - Text to escape
 * @returns {string} - Escaped text safe for textContent only
 */
export function escapeText(text) {
  if (typeof text !== 'string' || !text) return '';
  
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/**
 * Sanitize HTML to prevent XSS attacks
 * This function uses the browser's text node escaping
 * @param {string} html - HTML string to sanitize
 * @returns {string} - Sanitized HTML string with escaped entities
 */
export function sanitizeHtml(html) {
  if (typeof html !== 'string' || !html) return '';
  
  const div = document.createElement('div');
  div.textContent = html;
  return div.innerHTML;
}

/**
 * Validate URL to prevent open redirect attacks
 * @param {string} url - URL to validate
 * @returns {boolean} - Whether the URL is safe
 */
export function isValidRedirectUrl(url) {
  if (!url) return false;
  
  try {
    const parsed = new URL(url, window.location.origin);
    
    // Only allow same-origin redirects
    if (parsed.origin !== window.location.origin) {
      return false;
    }
    
    // Prevent javascript: protocol
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return false;
    }
    
    return true;
  } catch {
    return false;
  }
}

/**
 * Create a secure download link for blob data
 * @param {Blob} blob - Blob data to download
 * @param {string} filename - Filename for download
 */
export function secureDownload(blob, filename) {
  // Validate blob type
  const safeTypes = [
    'text/csv',
    'application/json',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/octet-stream'
  ];
  
  if (!safeTypes.includes(blob.type)) {
    throw new Error(`Unsafe file type: ${blob.type}`);
  }
  
  // Create download link
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  
  // Security: validate filename to prevent path traversal
  const safeFilename = filename.replace(/[^a-zA-Z0-9._-]/g, '_');
  link.download = safeFilename;
  
  // Trigger download
  document.body.appendChild(link);
  link.click();
  
  // Cleanup
  setTimeout(() => {
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
  }, 100);
}

export default {
  getCsrfToken,
  escapeText,
  sanitizeHtml,
  isValidRedirectUrl,
  secureDownload
};
