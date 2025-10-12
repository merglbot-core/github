/**
 * Central configuration for API endpoints
 * This file defines all API endpoints used in the application
 */

export const API_ENDPOINTS = {
  // Metrics endpoints
  METRICS_RELEASES: '/metrics/releases',
  METRICS_BOTS: '/metrics/bots',
  METRICS_SECURITY: '/metrics/security',
  METRICS_DEPLOYMENTS: '/metrics/deployments',
  
  // Service endpoints
  SERVICES_VERSIONS: '/services/versions',
  
  // Deployment endpoints
  DEPLOYMENTS_RECENT: '/deployments/recent',
  
  // Rollback endpoints
  ROLLBACKS: '/rollbacks',
  
  // Security endpoints
  SECURITY_INCIDENTS: '/security/incidents',
  
  // Bot endpoints
  BOTS_EFFECTIVENESS: '/bots/effectiveness',
  
  // Review endpoints
  REVIEWS_QUARTERLY: '/reviews/quarterly',
  
  // Audit endpoints
  AUDITS_QUARTERLY_TRIGGER: '/audits/quarterly/trigger',
  
  // Export endpoints
  METRICS_EXPORT: '/metrics/export',
  
  // Alert endpoints
  ALERTS_CONFIGS: '/alerts/configs',
  ALERTS_TEST: '/alerts/test'
};

// Export for backward compatibility
export default API_ENDPOINTS;