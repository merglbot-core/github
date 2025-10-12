#!/bin/sh
set -e

# Default API_URL if not provided
: ${API_URL:=http://localhost:8080}

# Basic URL validation
# Improved URL validation: scheme, domain/localhost/IPv4, optional port/path
# Relaxed URL validation: scheme, any valid hostname (single-label or domain), optional port, optional path
if ! echo "$API_URL" | grep -Eq '^https?://[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)*(:[0-9]{1,5})?(/[a-zA-Z0-9/_-]*)?$'; then
  echo "Error: Invalid API_URL format: ${API_URL}"
  exit 1
fi

echo "Configuring nginx with API_URL: ${API_URL}"

# Use the nginx.conf as template and substitute environment variables
# More efficient approach - direct substitution
envsubst '${API_URL}' < /etc/nginx/nginx.conf > /tmp/nginx.conf
mv /tmp/nginx.conf /etc/nginx/nginx.conf

echo "Starting nginx..."
exec nginx -g 'daemon off;'