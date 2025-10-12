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

# Install curl for health checks and gettext for envsubst
RUN apk add --no-cache curl gettext

# Copy nginx configuration as template
COPY nginx.conf /etc/nginx/nginx.conf.template

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy entrypoint script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Create non-root user with proper permissions
RUN adduser -D -u 1001 dashboard && \
    chown -R dashboard:dashboard /usr/share/nginx/html && \
    chown -R dashboard:dashboard /var/cache/nginx && \
    chown -R dashboard:dashboard /var/log/nginx && \
    chown -R dashboard:dashboard /etc/nginx && \
    touch /var/run/nginx.pid && \
    chown -R dashboard:dashboard /var/run/nginx.pid

# Switch to non-root user
USER dashboard

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Use entrypoint script for environment variable substitution
ENTRYPOINT ["/docker-entrypoint.sh"]
