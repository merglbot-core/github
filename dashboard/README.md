# Merglbot Release Dashboard

A comprehensive React-based dashboard for tracking release metrics, bot effectiveness, and security compliance across all Merglbot repositories.

## Features

- **Real-time Metrics Dashboard**: Overview of key performance indicators
- **Release Tracking**: Monitor deployment frequency, rollback rates, and MTTR
- **Bot Analytics**: Track AI bot effectiveness and usage patterns
- **Security Monitoring**: Compliance tracking and incident management
- **Responsive Design**: Works seamlessly on desktop and mobile devices
- **Dark Mode Support**: Eye-friendly interface for extended use

## Tech Stack

- **Frontend**: React 18, Material-UI v5, Recharts
- **State Management**: React Query (TanStack Query)
- **Build Tool**: Vite
- **Styling**: Emotion, MUI theming
- **Container**: Docker with multi-stage build
- **Server**: Nginx (production)

## Prerequisites

- Node.js 18+ and npm 9+
- Docker (for containerized deployment)
- Access to backend API endpoints

## Installation

1. Clone the repository:
```bash
git clone https://github.com/merglbot-core/github.git
cd github/dashboard
```

2. Install dependencies:
```bash
npm install
```

3. Create environment file:
```bash
cp .env.example .env
```

4. Configure environment variables:
```env
VITE_API_URL=http://localhost:8080
VITE_ENABLE_MOCK_DATA=false
```

## Development

Start the development server:
```bash
npm run dev
```

The dashboard will be available at `http://localhost:3000`

### Available Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm run preview` - Preview production build
- `npm run lint` - Run ESLint
- `npm run test` - Run tests

## Production Build

### Local Build

```bash
npm run build
```

The built files will be in the `dist/` directory.

### Docker Build

Build the Docker image:
```bash
docker build -t merglbot-release-dashboard .
```

Run the container:
```bash
docker run -p 8080:8080 \
  -e API_URL=https://api.merglbot.ai \
  merglbot-release-dashboard
```

## Deployment

### Cloud Run Deployment

```bash
# Build and push to Artifact Registry
gcloud builds submit --tag europe-docker.pkg.dev/mb-artifacts-prd/merglbot/release-dashboard

# Deploy to Cloud Run
gcloud run deploy release-dashboard \
  --image europe-docker.pkg.dev/mb-artifacts-prd/merglbot/release-dashboard \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars="API_URL=https://api.merglbot.ai"
```

## Architecture

```
dashboard/
├── src/
│   ├── components/       # React components
│   │   ├── Dashboard/     # Main dashboard
│   │   ├── ReleaseMetrics/
│   │   ├── BotMetrics/
│   │   ├── SecurityMonitoring/
│   │   └── Common/        # Shared components
│   ├── hooks/            # Custom React hooks
│   ├── services/         # API services
│   ├── utils/            # Utility functions
│   └── theme.js          # MUI theme configuration
├── public/               # Static assets
├── Dockerfile            # Container configuration
└── nginx.conf            # Nginx configuration
```

## API Integration

The dashboard integrates with the following backend endpoints:

- `/api/metrics/releases` - Release metrics
- `/api/metrics/bots` - Bot effectiveness metrics
- `/api/metrics/security` - Security metrics
- `/api/deployments` - Deployment history
- `/api/services/versions` - Service version tracking

## Security Features

- Content Security Policy (CSP) headers
- XSS protection
- CORS configuration
- Authentication via IAP (production)
- Non-root container user
- Security headers in nginx

## Performance Optimizations

- Code splitting with dynamic imports
- Lazy loading of routes
- Image optimization
- Gzip compression
- Browser caching for static assets
- React Query caching with 5-minute refresh

## Testing

Run unit tests:
```bash
npm test
```

Run tests in watch mode:
```bash
npm run test:watch
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

Private - Merglbot Internal Use Only

## Support

For issues or questions, contact the platform team in #platform channel.