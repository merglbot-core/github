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
  const error = queries.find(query => query.error)?.error;

  return {
    releaseMetrics: queries[0].data,
    botMetrics: queries[1].data,
    securityMetrics: queries[2].data,
    deploymentMetrics: queries[3].data,
    isLoading,
    error,
    refetchAll: () => queries.forEach(query => query.refetch()),
  };
}

export default useMetrics;