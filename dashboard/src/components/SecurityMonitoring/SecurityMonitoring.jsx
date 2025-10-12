import React from 'react';
import { Box, Typography, Paper, Grid } from '@mui/material';

function SecurityMonitoring() {
  return (
    <Box>
      <Typography variant="h4" gutterBottom>Security Monitoring</Typography>
      <Typography variant="body1" color="text.secondary" paragraph>
        Track security compliance and incidents
      </Typography>
      <Grid container spacing={3}>
        <Grid item xs={12}>
          <Paper sx={{ p: 3, textAlign: 'center' }}>
            <Typography variant="h6">Security Dashboard</Typography>
            <Typography variant="body2" color="text.secondary">
              Secret leak tracking, gitignore compliance, and audit scheduling will be displayed here
            </Typography>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
}

export default SecurityMonitoring;