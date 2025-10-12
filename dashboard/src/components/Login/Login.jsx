import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  Alert,
  Container
} from '@mui/material';
import LockOutlinedIcon from '@mui/icons-material/LockOutlined';

function Login() {
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setLoading(true);

    try {
      // In production, this would be handled by IAP
      // For development, we can use a simple token-based auth
      const formData = new FormData(event.currentTarget);
      const email = formData.get('email');
      const password = formData.get('password');

      // Mock authentication - replace with actual API call
      if (email && password) {
        // SECURITY NOTE: This is ONLY for development/demo purposes
        // Production authentication is handled by Google IAP (Identity-Aware Proxy)
        // which provides:
        // - No client-side token storage (IAP handles auth at proxy level)
        // - Automatic token refresh and session management
        // - Protection against XSS, CSRF, and session hijacking
        // - Integration with Google Workspace SSO

        // Check if we're in development environment
        const isDevelopment = 
          (import.meta?.env?.MODE === 'development') ||
          window.location.hostname === 'localhost' ||
          window.location.hostname === '127.0.0.1';

        if (isDevelopment) {
          // Basic validation for development environment
          if (email === 'admin@example.com' && password === 'devpassword') {
            // SessionStorage is used here only for local development mock
            sessionStorage.setItem('auth_token', 'mock_token_' + Date.now());
            navigate('/dashboard');
          } else {
            setError('Invalid credentials. For development, use admin@example.com/devpassword');
          }
        } else {
          // In production, this should never execute as auth is handled by IAP
          console.error('Mock authentication attempted in production environment');
          setError('Authentication not available. Please use SSO.');
        }
      } else {
        setError('Please enter email and password');
      }
    } catch (err) {
      setError('Authentication failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container component="main" maxWidth="xs">
      <Box
        sx={{
          marginTop: 8,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
        }}
      >
        <Paper
          elevation={3}
          sx={{
            padding: 4,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            width: '100%'
          }}
        >
          <LockOutlinedIcon sx={{ m: 1, fontSize: 40, color: 'primary.main' }} />
          <Typography component="h1" variant="h5">
            Merglbot Dashboard
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1, mb: 3 }}>
            Sign in to access the Release Dashboard
          </Typography>
          
          {error && (
            <Alert severity="error" sx={{ width: '100%', mb: 2 }}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit} noValidate sx={{ mt: 1, width: '100%' }}>
            <TextField
              margin="normal"
              required
              fullWidth
              id="email"
              label="Email Address"
              name="email"
              autoComplete="email"
              autoFocus
            />
            <TextField
              margin="normal"
              required
              fullWidth
              name="password"
              label="Password"
              type="password"
              id="password"
              autoComplete="current-password"
            />
            <Button
              type="submit"
              fullWidth
              variant="contained"
              sx={{ mt: 3, mb: 2 }}
              disabled={loading}
            >
              {loading ? 'Signing in...' : 'Sign In'}
            </Button>
            <Typography variant="caption" color="text.secondary" align="center">
              Protected by Identity-Aware Proxy (IAP)
            </Typography>
          </Box>
        </Paper>
      </Box>
    </Container>
  );
}

export default Login;