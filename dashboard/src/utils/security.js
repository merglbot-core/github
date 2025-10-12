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
    // Development mode - generate a temporary token
    // In production, this should never happen
    if (process.env.NODE_ENV === 'development') {
      return 'dev_csrf_' + Math.random().toString(36).substr(2, 9);
    }
    
    // In production, missing CSRF token is a security issue
    console.error('CSRF token not found. Server must inject token in production.');
    return null;
  }
  
  return token;
}

/**
 * Sanitize HTML to prevent XSS attacks
 * This function escapes HTML entities to prevent script injection
 * @param {string} html - HTML string to sanitize
 * @returns {string} - Sanitized HTML string with escaped entities
 */
export function sanitizeHtml(html) {
  if (!html) return '';
  
  // Create a text node which automatically escapes HTML entities
  const div = document.createElement('div');
  const textNode = document.createTextNode(html);
  div.appendChild(textNode);
  
  // Return escaped HTML - safe to use with innerHTML if needed
  // This converts <script> to &lt;script&gt; etc.
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
  sanitizeHtml,
  isValidRedirectUrl,
  secureDownload
};