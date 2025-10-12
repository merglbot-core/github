import React from 'react';
import {
  Grid,
  Paper,
  Typography,
  Box,
  Chip,
  Stack,
  Alert
} from '@mui/material';
import {
  TrendingUp,
  TrendingDown,
  CheckCircle,
  Warning,
  Speed,
  BugReport,
  Security,
  RocketLaunch
} from '@mui/icons-material';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Area,
  AreaChart
} from 'recharts';
import useMetrics from '../../hooks/useMetrics';
import KPICard from './KPICard';
import LoadingState from '../Common/LoadingState';

const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884D8'];

function Dashboard() {
  const { 
    releaseMetrics, 
    botMetrics, 
    securityMetrics, 
    isLoading, 
    error 
  } = useMetrics();

  if (isLoading) return <LoadingState />;
  if (error) return <Alert severity="error">Error loading metrics: {error.message}</Alert>;

  const deploymentData = [
    { name: 'Mon', success: 12, failed: 1, rollback: 0 },
    { name: 'Tue', success: 15, failed: 2, rollback: 1 },
    { name: 'Wed', success: 18, failed: 0, rollback: 0 },
    { name: 'Thu', success: 14, failed: 1, rollback: 2 },
    { name: 'Fri', success: 20, failed: 0, rollback: 0 },
    { name: 'Sat', success: 8, failed: 0, rollback: 0 },
    { name: 'Sun', success: 5, failed: 1, rollback: 0 },
  ];

  const botUsageData = [
    { name: 'Copilot', value: 45, usage: 320 },
    { name: 'Cursor', value: 30, usage: 210 },
    { name: 'Claude', value: 20, usage: 140 },
    { name: 'Manual', value: 5, usage: 35 },
  ];

  const leadTimeData = [
    { month: 'Jul', time: 4.2 },
    { month: 'Aug', time: 3.8 },
    { month: 'Sep', time: 3.5 },
    { month: 'Oct', time: 3.1 },
    { month: 'Nov', time: 2.9 },
    { month: 'Dec', time: 2.7 },
  ];

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Release Dashboard Overview
      </Typography>
      <Typography variant="body1" color="text.secondary" paragraph>
        Real-time metrics and monitoring across all repositories
      </Typography>

      {/* KPI Cards */}
      <Grid container spacing={3} sx={{ mb: 3 }}>
        <Grid item xs={12} sm={6} md={3}>
          <KPICard
            title="Total Deployments"
            value="92"
            change="+12%"
            trend="up"
            icon={<RocketLaunch />}
            color="primary"
            subtitle="This week"
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <KPICard
            title="Success Rate"
            value="97.8%"
            change="+2.3%"
            trend="up"
            icon={<CheckCircle />}
            color="success"
            subtitle="Last 30 days"
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <KPICard
            title="MTTR"
            value="24 min"
            change="-15%"
            trend="down"
            icon={<Speed />}
            color="warning"
            subtitle="Avg recovery time"
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <KPICard
            title="Security Score"
            value="94/100"
            change="+5"
            trend="up"
            icon={<Security />}
            color="info"
            subtitle="Compliance rate"
          />
        </Grid>
      </Grid>

      {/* Charts Row 1 */}
      <Grid container spacing={3} sx={{ mb: 3 }}>
        {/* Deployment Trends */}
        <Grid item xs={12} md={8}>
          <Paper sx={{ p: 2, height: '100%' }} className="dashboard-card">
            <Typography variant="h6" gutterBottom>
              Deployment Trends
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={deploymentData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="success" stackId="a" fill="#4caf50" name="Success" />
                <Bar dataKey="failed" stackId="a" fill="#f44336" name="Failed" />
                <Bar dataKey="rollback" stackId="a" fill="#ff9800" name="Rollback" />
              </BarChart>
            </ResponsiveContainer>
          </Paper>
        </Grid>

        {/* Bot Usage Distribution */}
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2, height: '100%' }} className="dashboard-card">
            <Typography variant="h6" gutterBottom>
              Bot Usage Distribution
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={botUsageData}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                  outerRadius={80}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {botUsageData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </Paper>
        </Grid>
      </Grid>

      {/* Charts Row 2 */}
      <Grid container spacing={3} sx={{ mb: 3 }}>
        {/* Lead Time Trend */}
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 2 }} className="dashboard-card">
            <Typography variant="h6" gutterBottom>
              Lead Time Trend (days)
            </Typography>
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={leadTimeData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="month" />
                <YAxis />
                <Tooltip />
                <Area type="monotone" dataKey="time" stroke="#1976d2" fill="#1976d2" fillOpacity={0.3} />
              </AreaChart>
            </ResponsiveContainer>
          </Paper>
        </Grid>

        {/* Security Incidents */}
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 2 }} className="dashboard-card">
            <Typography variant="h6" gutterBottom>
              Security Status
            </Typography>
            <Stack spacing={2}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Typography variant="body2">Secret Scanning</Typography>
                <Chip label="Active" color="success" size="small" />
              </Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Typography variant="body2">Pre-commit Hooks</Typography>
                <Chip label="94% Adoption" color="primary" size="small" />
              </Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Typography variant="body2">Gitignore Compliance</Typography>
                <Chip label="98% Compliant" color="success" size="small" />
              </Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Typography variant="body2">Last Security Audit</Typography>
                <Chip label="2 days ago" color="info" size="small" />
              </Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Typography variant="body2">Incidents (30d)</Typography>
                <Chip label="0 Critical" color="success" size="small" />
              </Box>
            </Stack>
          </Paper>
        </Grid>
      </Grid>

      {/* Recent Activity */}
      <Grid container spacing={3}>
        <Grid item xs={12}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="h6" gutterBottom>
              Recent Deployments
            </Typography>
            <Stack spacing={1}>
              {[
                { service: 'btf-api', version: 'v2.3.1', status: 'success', time: '2 hours ago' },
                { service: 'aaas-api', version: 'v1.8.5', status: 'success', time: '4 hours ago' },
                { service: 'portal', version: 'v3.1.0', status: 'success', time: '6 hours ago' },
                { service: 'admin', version: 'v2.0.3', status: 'rollback', time: '8 hours ago' },
              ].map((deployment) => (
                <Box
                  key={`${deployment.service}-${deployment.version}-${deployment.time}`}
                  sx={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    p: 1.5,
                    borderRadius: 1,
                    backgroundColor: 'background.default',
                    '&:hover': {
                      backgroundColor: 'action.hover'
                    }
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                    <Typography variant="body1" fontWeight="medium">
                      {deployment.service}
                    </Typography>
                    <Chip label={deployment.version} size="small" />
                  </Box>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                    <Chip 
                      label={deployment.status} 
                      size="small"
                      color={deployment.status === 'success' ? 'success' : 'warning'}
                    />
                    <Typography variant="caption" color="text.secondary">
                      {deployment.time}
                    </Typography>
                  </Box>
                </Box>
              ))}
            </Stack>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
}

export default Dashboard;