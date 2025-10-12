import axios from 'axios';
import { API_ENDPOINTS } from '../config/api';

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
    // Add auth token if available
    const token = localStorage.getItem('auth_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    
    // Add CSRF token for mutating operations
    if (['post', 'put', 'delete', 'patch'].includes(config.method)) {
      const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
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
      localStorage.removeItem('auth_token');
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
    const response = await apiClient.get('/metrics/deployments', {
      params: { period }
    });
    return response.data;
  },

  // Get service versions
  async getServiceVersions() {
    const response = await apiClient.get('/services/versions');
    return response.data;
  },

  // Get recent deployments
  async getRecentDeployments(limit = 10) {
    const response = await apiClient.get('/deployments/recent', {
      params: { limit }
    });
    return response.data;
  },

  // Get rollback history
  async getRollbackHistory(period = '30d') {
    const response = await apiClient.get('/rollbacks', {
      params: { period }
    });
    return response.data;
  },

  // Get security incidents
  async getSecurityIncidents(period = '30d') {
    const response = await apiClient.get('/security/incidents', {
      params: { period }
    });
    return response.data;
  },

  // Get bot effectiveness
  async getBotEffectiveness() {
    const response = await apiClient.get('/bots/effectiveness');
    return response.data;
  },

  // Get quarterly review data
  async getQuarterlyReview(quarter) {
    const response = await apiClient.get('/reviews/quarterly', {
      params: { quarter }
    });
    return response.data;
  },

  // Trigger quarterly audit
  async triggerQuarterlyAudit() {
    const response = await apiClient.post('/audits/quarterly/trigger');
    return response.data;
  },

  // Export metrics to CSV
  async exportMetrics(type, period) {
    const response = await apiClient.get(`/metrics/export/${type}`, {
      params: { period },
      responseType: 'blob'
    });
    return response.data;
  },

  // Get alert configurations
  async getAlertConfigs() {
    const response = await apiClient.get('/alerts/configs');
    return response.data;
  },

  // Update alert configuration
  async updateAlertConfig(id, config) {
    const response = await apiClient.put(`/alerts/configs/${id}`, config);
    return response.data;
  },

  // Test alert
  async testAlert(alertType) {
    const endpoint =
      typeof API_ENDPOINTS.ALERTS_TEST === 'function'
        ? API_ENDPOINTS.ALERTS_TEST(alertType)
        : API_ENDPOINTS.ALERTS_TEST.replace(':type', alertType);
    const response = await apiClient.post(endpoint);
    return response.data;
  },
};

export default metricsService;