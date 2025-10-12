# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies (including devDependencies for build)
RUN npm ci

# Copy source code
COPY . .

# Build the application
RUN npm run build

# Production stage
FROM nginx:1.24-alpine

# Install curl for health checks, gettext for envsubst, and su-exec for privilege dropping
RUN apk add --no-cache curl gettext su-exec

# Copy nginx configuration (will be used as template by entrypoint)
COPY nginx.conf /etc/nginx/nginx.conf

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy entrypoint script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Create non-root user with proper permissions
# Note: /etc/nginx is NOT owned by dashboard for security (read-only config)
RUN adduser -D -u 1001 dashboard && \
    chown -R dashboard:dashboard /usr/share/nginx/html && \
    chown -R dashboard:dashboard /var/cache/nginx && \
    chown -R dashboard:dashboard /var/log/nginx && \
    touch /var/run/nginx.pid && \
    chown dashboard:dashboard /var/run/nginx.pid

# Make entrypoint executable
RUN chmod +x /docker-entrypoint.sh

# Entrypoint runs as root, drops to dashboard user before starting nginx

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Use entrypoint script for environment variable substitution
ENTRYPOINT ["/docker-entrypoint.sh"]
