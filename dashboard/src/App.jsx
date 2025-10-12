import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Box, Container } from '@mui/material';
import Layout from './components/Layout/Layout';
import Dashboard from './components/Dashboard/Dashboard';
import ReleaseMetrics from './components/ReleaseMetrics/ReleaseMetrics';
import BotMetrics from './components/BotMetrics/BotMetrics';
import SecurityMonitoring from './components/SecurityMonitoring/SecurityMonitoring';
import Settings from './components/Settings/Settings';
import NotFound from './components/NotFound/NotFound';
import ErrorBoundary from './components/ErrorBoundary/ErrorBoundary';

function App() {
  return (
    <ErrorBoundary>
      <Layout>
        <Container maxWidth={false} sx={{ mt: 4, mb: 4 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/releases" element={<ReleaseMetrics />} />
            <Route path="/bots" element={<BotMetrics />} />
            <Route path="/security" element={<SecurityMonitoring />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Container>
      </Layout>
    </ErrorBoundary>
  );
}

export default App;