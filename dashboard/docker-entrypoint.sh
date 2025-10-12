#!/bin/sh
set -e

# Default API_URL if not provided
: ${API_URL:=http://localhost:8080}

echo "Configuring nginx with API_URL: ${API_URL}"

# Create nginx config from template with environment variable substitution
envsubst '${API_URL}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

echo "Starting nginx..."
exec nginx -g 'daemon off;'