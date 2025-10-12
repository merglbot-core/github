import axios from 'axios';
import { API_ENDPOINTS } from '../config/api';
import { getCsrfToken } from '../utils/security';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add request interceptor for authentication and CSRF protection
apiClient.interceptors.request.use(
  (config) => {
    // Add auth token if available (using sessionStorage for better security)
  const token = sessionStorage.getItem('auth_token');
  if (token && token.startsWith('mock_token_')) {
    config.headers.Authorization = `Bearer ${token}`;
      config.headers.Authorization = `Bearer ${token}`;
    }
    
    // Add CSRF token for mutating operations
    if (['post', 'put', 'delete', 'patch'].includes(config.method)) {
      const csrfToken = getCsrfToken();
      if (csrfToken) {
        config.headers['X-CSRF-Token'] = csrfToken;
      }
    }
    
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Add response interceptor for error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && window.location.pathname !== '/login') {
      // Handle unauthorized access, but avoid redirect loop
      sessionStorage.removeItem('auth_token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// Valid periods for metrics
const VALID_PERIODS = ['7d', '30d', '90d', '1y'];

const metricsService = {
  // Release metrics
  async getReleaseMetrics(period = '30d') {
    // Validate period parameter
    if (!VALID_PERIODS.includes(period)) {
      throw new Error(`Invalid period: ${period}. Valid periods are: ${VALID_PERIODS.join(', ')}`);
    }
    
    const response = await apiClient.get(API_ENDPOINTS.METRICS_RELEASES, {
      params: { period }
    });
    return response.data;
  },

  // Bot metrics
  async getBotMetrics(period = '30d') {
    // Validate period parameter
    if (!VALID_PERIODS.includes(period)) {
      throw new Error(`Invalid period: ${period}. Valid periods are: ${VALID_PERIODS.join(', ')}`);
    }
    
    const response = await apiClient.get(API_ENDPOINTS.METRICS_BOTS, {
      params: { period }
    });
    return response.data;
  },

  // Security metrics
  async getSecurityMetrics() {
    const response = await apiClient.get(API_ENDPOINTS.METRICS_SECURITY);
    return response.data;
  },

  // Deployment metrics
  async getDeploymentMetrics(period = '7d') {
    // Validate period parameter
    if (!VALID_PERIODS.includes(period)) {
      throw new Error(`Invalid period: ${period}. Valid periods are: ${VALID_PERIODS.join(', ')}`);
    }
    
    const response = await apiClient.get(API_ENDPOINTS.METRICS_DEPLOYMENTS, {
      params: { period }
    });
    return response.data;
  },

  // Get service versions
  async getServiceVersions() {
    const response = await apiClient.get(API_ENDPOINTS.SERVICES_VERSIONS);
    return response.data;
  },

  // Get recent deployments
  async getRecentDeployments(limit = 10) {
    const response = await apiClient.get(API_ENDPOINTS.DEPLOYMENTS_RECENT, {
      params: { limit }
    });
    return response.data;
  },

  // Get rollback history
  async getRollbackHistory(period = '30d') {
    // Validate period parameter
    if (!VALID_PERIODS.includes(period)) {
      throw new Error(`Invalid period: ${period}. Valid periods are: ${VALID_PERIODS.join(', ')}`);
    }
    
    const response = await apiClient.get(API_ENDPOINTS.ROLLBACKS, {
      params: { period }
    });
    return response.data;
  },

  // Get security incidents
  async getSecurityIncidents(period = '30d') {
    // Validate period parameter
    if (!VALID_PERIODS.includes(period)) {
      throw new Error(`Invalid period: ${period}. Valid periods are: ${VALID_PERIODS.join(', ')}`);
    }
    
    const response = await apiClient.get(API_ENDPOINTS.SECURITY_INCIDENTS, {
      params: { period }
    });
    return response.data;
  },

  // Get bot effectiveness
  async getBotEffectiveness() {
    const response = await apiClient.get(API_ENDPOINTS.BOTS_EFFECTIVENESS);
    return response.data;
  },

  // Get quarterly review data
  async getQuarterlyReview(quarter) {
    const response = await apiClient.get(API_ENDPOINTS.REVIEWS_QUARTERLY, {
      params: { quarter }
    });
    return response.data;
  },

  // Trigger quarterly audit
  async triggerQuarterlyAudit() {
    const response = await apiClient.post(API_ENDPOINTS.AUDITS_QUARTERLY_TRIGGER);
    return response.data;
  },

  // Export metrics to CSV
  async exportMetrics(type, period) {
    // Validate period parameter
    if (!VALID_PERIODS.includes(period)) {
      throw new Error(`Invalid period: ${period}. Valid periods are: ${VALID_PERIODS.join(', ')}`);
    }
    
    // Validate export type
    const validTypes = ['csv', 'json', 'xlsx'];
    if (!validTypes.includes(type)) {
      throw new Error(`Invalid export type: ${type}. Valid types are: ${validTypes.join(', ')}`);
    }
    
    const response = await apiClient.get(`${API_ENDPOINTS.METRICS_EXPORT}/${type}`, {
      params: { period },
      responseType: 'blob'
    });
    
    // Validate content-type to prevent XSS via blob rendering
    const contentType = response.headers['content-type'];
    const allowedTypes = [
      'text/csv',
      'application/json',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'application/octet-stream'
    ];
    
    if (!allowedTypes.includes(contentType)) {
      throw new Error(`Unsafe content type returned: ${contentType}. Export aborted for security.`);
    }
    
    return response.data;
  },

  // Get alert configurations
  async getAlertConfigs() {
    const response = await apiClient.get(API_ENDPOINTS.ALERTS_CONFIGS);
    return response.data;
  },

  // Update alert configuration
  async updateAlertConfig(id, config) {
    const response = await apiClient.put(`${API_ENDPOINTS.ALERTS_CONFIGS}/${id}`, config);
    return response.data;
  },

  // Test alert
  async testAlert(alertType) {
    const response = await apiClient.post(`${API_ENDPOINTS.ALERTS_TEST}/${alertType}`);
    return response.data;
  },
};

export default metricsService;