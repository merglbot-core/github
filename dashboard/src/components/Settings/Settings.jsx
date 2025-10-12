import React from 'react';
import { Box, Typography, Paper, Grid } from '@mui/material';

function Settings() {
  return (
    <Box>
      <Typography variant="h4" gutterBottom>Settings</Typography>
      <Typography variant="body1" color="text.secondary" paragraph>
        Configure dashboard preferences and alerts
      </Typography>
      <Grid container spacing={3}>
        <Grid item xs={12}>
          <Paper sx={{ p: 3, textAlign: 'center' }}>
            <Typography variant="h6">Dashboard Settings</Typography>
            <Typography variant="body2" color="text.secondary">
              Alert configurations and notification preferences will be managed here
            </Typography>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
}

export default Settings;