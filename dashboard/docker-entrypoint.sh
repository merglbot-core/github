#!/bin/sh
set -e

# Default API_URL if not provided
: ${API_URL:=http://localhost:8080}

# Enhanced URL validation to prevent SSRF attacks
# Validates: scheme (http/https), domain/localhost/IP, port, and path
# Also checks for common bypass patterns
validate_url() {
  local url="$1"
  
  # Check for empty URL
  if [ -z "$url" ]; then
    return 1
  fi
  
  # Check for basic URL structure
  if ! echo "$url" | grep -Eq '^https?://[^/]+'; then
    return 1
  fi
  
  # Reject URLs with authentication info (user:pass@)
  if echo "$url" | grep -q '@'; then
    return 1
  fi
  
  # Reject URLs with multiple slashes after protocol (http:///)
  if echo "$url" | grep -q '://[/]{2,}'; then
    return 1
  fi
  
  # Accept localhost, 127.0.0.1, proper domains, IPv4 addresses and single-label hostnames
  # Relaxed to support single-label hostnames (e.g., http://api-server)
  if echo "$url" | grep -Eq '^https?://((([a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)*)|((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)))(:[0-9]{1,5})?(/[a-zA-Z0-9/_-]*)?$'; then
    return 0
  fi
  
  return 1
}

if ! validate_url "$API_URL"; then
  echo "Error: Invalid API_URL format: ${API_URL}"
  echo "URL must be in format: http[s]://domain[:port][/path]"
  echo "Examples: http://localhost:8080, https://api.example.com"
  exit 1
fi

echo "Configuring nginx with API_URL: ${API_URL}"

# Use the nginx.conf as template and substitute environment variables
# More efficient approach - direct substitution
envsubst '${API_URL}' < /etc/nginx/nginx.conf > /tmp/nginx.conf
mv /tmp/nginx.conf /etc/nginx/nginx.conf

echo "Starting nginx..."
# Drop privileges to dashboard user before starting nginx
exec su-exec dashboard nginx -g 'daemon off;'
