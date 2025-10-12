#!/bin/sh
set -e

# Default API_URL if not provided
: ${API_URL:=http://localhost:8080}

# Basic URL validation
if ! echo "$API_URL" | grep -Eq '^https?://[a-zA-Z0-9.-]+(\.[a-zA-Z0-9-]+)*(:[0-9]+)?(/.*)?$'; then
  echo "Error: Invalid API_URL format: ${API_URL}"
  exit 1
fi

echo "Configuring nginx with API_URL: ${API_URL}"

# Create nginx config from template with environment variable substitution
envsubst '${API_URL}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

echo "Starting nginx..."
exec nginx -g 'daemon off;'