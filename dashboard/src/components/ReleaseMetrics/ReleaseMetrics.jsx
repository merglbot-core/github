import React, { useState } from 'react';
import {
  Box,
  Paper,
  Typography,
  Grid,
  Card,
  CardContent,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Button,
  IconButton,
  Tooltip
} from '@mui/material';
import {
  Download as DownloadIcon,
  Refresh as RefreshIcon,
  Timeline as TimelineIcon
} from '@mui/icons-material';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as ChartTooltip,
  Legend,
  ResponsiveContainer
} from 'recharts';
import LoadingState from '../Common/LoadingState';
import useMetrics from '../../hooks/useMetrics';

function ReleaseMetrics() {
  const [period, setPeriod] = useState('30d');
  const [service, setService] = useState('all');
  const { releaseMetrics, isLoading, error, refetchAll } = useMetrics();

  if (isLoading) return <LoadingState type="dashboard" />;

  // Sample data for demonstration
  const releaseFrequencyData = [
    { date: 'Oct 1', releases: 5, rollbacks: 0 },
    { date: 'Oct 2', releases: 8, rollbacks: 1 },
    { date: 'Oct 3', releases: 3, rollbacks: 0 },
    { date: 'Oct 4', releases: 12, rollbacks: 1 },
    { date: 'Oct 5', releases: 7, rollbacks: 0 },
    { date: 'Oct 6', releases: 4, rollbacks: 0 },
    { date: 'Oct 7', releases: 9, rollbacks: 2 },
  ];

  const serviceVersions = [
    { service: 'btf-api', current: 'v2.3.1', previous: 'v2.3.0', deployedAt: '2025-10-12 10:30', status: 'healthy' },
    { service: 'aaas-api', current: 'v1.8.5', previous: 'v1.8.4', deployedAt: '2025-10-12 08:15', status: 'healthy' },
    { service: 'portal', current: 'v3.1.0', previous: 'v3.0.9', deployedAt: '2025-10-12 06:45', status: 'healthy' },
    { service: 'admin', current: 'v2.0.3', previous: 'v2.0.2', deployedAt: '2025-10-11 18:20', status: 'warning' },
  ];

  const mttrData = [
    { month: 'Jul', mttr: 45 },
    { month: 'Aug', mttr: 38 },
    { month: 'Sep', mttr: 32 },
    { month: 'Oct', mttr: 24 },
  ];

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box>
          <Typography variant="h4" gutterBottom>
            Release Metrics
          </Typography>
          <Typography variant="body1" color="text.secondary">
            Track release frequency, rollback rates, and deployment health
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 2 }}>
          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel>Period</InputLabel>
            <Select value={period} onChange={(e) => setPeriod(e.target.value)} label="Period">
              <MenuItem value="7d">Last 7 days</MenuItem>
              <MenuItem value="30d">Last 30 days</MenuItem>
              <MenuItem value="90d">Last 90 days</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel>Service</InputLabel>
            <Select value={service} onChange={(e) => setService(e.target.value)} label="Service">
              <MenuItem value="all">All Services</MenuItem>
              <MenuItem value="btf-api">BTF API</MenuItem>
              <MenuItem value="aaas-api">AaaS API</MenuItem>
              <MenuItem value="portal">Portal</MenuItem>
              <MenuItem value="admin">Admin</MenuItem>
            </Select>
          </FormControl>
          <IconButton onClick={refetchAll} color="primary">
            <RefreshIcon />
          </IconButton>
          <Button startIcon={<DownloadIcon />} variant="outlined">
            Export
          </Button>
        </Box>
      </Box>

      {/* KPI Summary */}
      <Grid container spacing={3} sx={{ mb: 3 }}>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" gutterBottom>
                Total Releases
              </Typography>
              <Typography variant="h4">48</Typography>
              <Typography variant="caption" color="success.main">
                +15% from last period
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" gutterBottom>
                Rollback Rate
              </Typography>
              <Typography variant="h4">2.1%</Typography>
              <Typography variant="caption" color="success.main">
                -0.5% from last period
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" gutterBottom>
                Avg Lead Time
              </Typography>
              <Typography variant="h4">2.7d</Typography>
              <Typography variant="caption" color="success.main">
                -0.4d from last period
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" gutterBottom>
                MTTR
              </Typography>
              <Typography variant="h4">24 min</Typography>
              <Typography variant="caption" color="success.main">
                -8 min from last period
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Charts */}
      <Grid container spacing={3} sx={{ mb: 3 }}>
        <Grid item xs={12} md={8}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="h6" gutterBottom>
              Release Frequency & Rollbacks
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={releaseFrequencyData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis />
                <ChartTooltip />
                <Legend />
                <Bar dataKey="releases" fill="#1976d2" name="Releases" />
                <Bar dataKey="rollbacks" fill="#f44336" name="Rollbacks" />
              </BarChart>
            </ResponsiveContainer>
          </Paper>
        </Grid>
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="h6" gutterBottom>
              MTTR Trend
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={mttrData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="month" />
                <YAxis />
                <ChartTooltip />
                <Line type="monotone" dataKey="mttr" stroke="#ff9800" strokeWidth={2} name="MTTR (min)" />
              </LineChart>
            </ResponsiveContainer>
          </Paper>
        </Grid>
      </Grid>

      {/* Service Versions Table */}
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>
          Current Service Versions
        </Typography>
        <TableContainer>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Service</TableCell>
                <TableCell>Current Version</TableCell>
                <TableCell>Previous Version</TableCell>
                <TableCell>Deployed At</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {serviceVersions.map((row) => (
                <TableRow key={row.service}>
                  <TableCell>{row.service}</TableCell>
                  <TableCell>
                    <Chip label={row.current} size="small" color="primary" />
                  </TableCell>
                  <TableCell>{row.previous}</TableCell>
                  <TableCell>{row.deployedAt}</TableCell>
                  <TableCell>
                    <Chip 
                      label={row.status} 
                      size="small" 
                      color={row.status === 'healthy' ? 'success' : 'warning'}
                    />
                  </TableCell>
                  <TableCell>
                    <Tooltip title="View Timeline">
                      <IconButton size="small">
                        <TimelineIcon />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </Box>
  );
}

export default ReleaseMetrics;