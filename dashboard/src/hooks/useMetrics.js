import { useQuery, useQueries } from '@tanstack/react-query';
import metricsService from '../services/metricsService';

function useMetrics(options = {}) {
  const queries = useQueries({
    queries: [
      {
        queryKey: ['metrics', 'releases'],
        queryFn: metricsService.getReleaseMetrics,
        ...options,
      },
      {
        queryKey: ['metrics', 'bots'],
        queryFn: metricsService.getBotMetrics,
        ...options,
      },
      {
        queryKey: ['metrics', 'security'],
        queryFn: metricsService.getSecurityMetrics,
        ...options,
      },
      {
        queryKey: ['metrics', 'deployments'],
        queryFn: metricsService.getDeploymentMetrics,
        ...options,
      },
    ],
  });

  const isLoading = queries.some(query => query.isLoading);
  // Aggregate all errors instead of just the first one
  const errors = queries
    .filter(query => query.error)
    .map((query, i) => ({
      metric: query.queryKey?.[1] ?? `index-${i}`, // Fallback to array index if missing
      error: query.error,
    }));
  const error = errors.length > 0 ? errors : null;

  return {
    releaseMetrics: queries[0].data,
    botMetrics: queries[1].data,
    securityMetrics: queries[2].data,
    deploymentMetrics: queries[3].data,
    isLoading,
    error,
    errors, // Expose individual errors array
    refetchAll: () => queries.forEach(query => query.refetch()),
  };
}

export default useMetrics;