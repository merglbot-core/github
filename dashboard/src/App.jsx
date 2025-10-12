import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Container } from '@mui/material';
import Layout from './components/Layout/Layout';
import Dashboard from './components/Dashboard/Dashboard';
import ReleaseMetrics from './components/ReleaseMetrics/ReleaseMetrics';
import BotMetrics from './components/BotMetrics/BotMetrics';
import SecurityMonitoring from './components/SecurityMonitoring/SecurityMonitoring';
import Settings from './components/Settings/Settings';
import Login from './components/Login/Login';
import NotFound from './components/NotFound/NotFound';
import ErrorBoundary from './components/ErrorBoundary/ErrorBoundary';

function App() {
  return (
    <ErrorBoundary>
      <Routes>
        {/* Login route without Layout */}
        <Route path="/login" element={<Login />} />
        
        {/* All other routes with Layout */}
        <Route
          path="/*"
          element={
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
          }
        />
      </Routes>
    </ErrorBoundary>
  );
}

export default App;