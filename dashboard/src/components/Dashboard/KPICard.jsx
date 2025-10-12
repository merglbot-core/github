import React from 'react';
import {
  Card,
  CardContent,
  Typography,
  Box,
  Chip,
  Avatar
} from '@mui/material';
import {
  TrendingUp,
  TrendingDown,
  TrendingFlat
} from '@mui/icons-material';

function KPICard({ title, value, change, trend, icon, color = 'primary', subtitle, higherIsBetter = true }) {
  const getTrendIcon = () => {
    switch (trend) {
      case 'up':
        return <TrendingUp fontSize="small" />;
      case 'down':
        return <TrendingDown fontSize="small" />;
      default:
        return <TrendingFlat fontSize="small" />;
    }
  };

  const getTrendColor = () => {
    // Use the higherIsBetter prop directly from the function parameter
    if (trend === 'up') {
      return higherIsBetter ? 'success' : 'error';
    }
    if (trend === 'down') {
      return higherIsBetter ? 'error' : 'success';
    }
    return 'default';
  };

  return (
    <Card className="dashboard-card" sx={{ height: '100%' }}>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <Box sx={{ flex: 1 }}>
            <Typography color="text.secondary" gutterBottom variant="caption">
              {title}
            </Typography>
            <Typography variant="h4" component="div" fontWeight="bold">
              {value}
            </Typography>
            {subtitle && (
              <Typography variant="caption" color="text.secondary">
                {subtitle}
              </Typography>
            )}
            {change && (
              <Box sx={{ display: 'flex', alignItems: 'center', mt: 1 }}>
                <Chip
                  icon={getTrendIcon()}
                  label={change}
                  size="small"
                  color={getTrendColor()}
                  variant="outlined"
                />
              </Box>
            )}
          </Box>
          {icon && (
            <Avatar
              sx={{
                bgcolor: `${color}.light`,
                color: `${color}.dark`,
                width: 48,
                height: 48
              }}
            >
              {icon}
            </Avatar>
          )}
        </Box>
      </CardContent>
    </Card>
  );
}

export default KPICard;