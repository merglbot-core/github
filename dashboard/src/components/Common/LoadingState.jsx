import React from 'react';
import {
  Box,
  Skeleton,
  Grid,
  Paper
} from '@mui/material';

function LoadingState({ type = 'dashboard' }) {
  if (type === 'dashboard') {
    return (
      <Box>
        <Skeleton variant="text" width={300} height={40} sx={{ mb: 1 }} />
        <Skeleton variant="text" width={500} height={24} sx={{ mb: 3 }} />
        
        {/* KPI Cards */}
        <Grid container spacing={3} sx={{ mb: 3 }}>
          {[1, 2, 3, 4].map((i) => (
            <Grid item xs={12} sm={6} md={3} key={i}>
              <Paper sx={{ p: 2 }}>
                <Skeleton variant="text" width={100} height={20} />
                <Skeleton variant="text" width={150} height={40} />
                <Skeleton variant="text" width={80} height={20} />
              </Paper>
            </Grid>
          ))}
        </Grid>

        {/* Charts */}
        <Grid container spacing={3}>
          <Grid item xs={12} md={8}>
            <Paper sx={{ p: 2 }}>
              <Skeleton variant="text" width={150} height={30} sx={{ mb: 2 }} />
              <Skeleton variant="rectangular" height={300} />
            </Paper>
          </Grid>
          <Grid item xs={12} md={4}>
            <Paper sx={{ p: 2 }}>
              <Skeleton variant="text" width={150} height={30} sx={{ mb: 2 }} />
              <Skeleton variant="circular" width={200} height={200} sx={{ mx: 'auto' }} />
            </Paper>
          </Grid>
        </Grid>
      </Box>
    );
  }

  if (type === 'table') {
    return (
      <Paper sx={{ p: 2 }}>
        <Skeleton variant="text" width={200} height={30} sx={{ mb: 2 }} />
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} variant="rectangular" height={60} sx={{ mb: 1 }} />
        ))}
      </Paper>
    );
  }

  if (type === 'card') {
    return (
      <Paper sx={{ p: 2 }}>
        <Skeleton variant="text" width={150} height={30} sx={{ mb: 2 }} />
        <Skeleton variant="rectangular" height={200} />
      </Paper>
    );
  }

  // Default loading
  return (
    <Box sx={{ width: '100%' }}>
      <Skeleton variant="rectangular" width="100%" height={400} />
    </Box>
  );
}

export default LoadingState;