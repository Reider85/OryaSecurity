# Production Configuration Examples

This file contains production-ready configuration examples for the LLM Security Scanner.

## Environment Variables (.env.production)

```bash
# Database Configuration
POSTGRES_DB=scanner_prod
POSTGRES_USER=scanner_user
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-$(openssl rand -base64 32)}
POSTGRES_HOST=${POSTGRES_HOST:-localhost}
POSTGRES_PORT=5432

# Redis Configuration
REDIS_URL=redis://${REDIS_HOST:-localhost}:6379/0
REDIS_PASSWORD=${REDIS_PASSWORD:-$(openssl rand -base64 32)}

# LLM Provider Configuration
LLM_PROVIDER_URL=${LLM_PROVIDER_URL:-https://api.openai.com/v1/chat/completions}
LLM_MODEL=${LLM_MODEL:-gpt-3.5-turbo}
LLM_TIMEOUT_SECONDS=${LLM_TIMEOUT_SECONDS:-30}

# Security Configuration
API_KEY_DEFAULT=${API_KEY_DEFAULT:-$(openssl rand -base64 32)}
JWT_SECRET=${JWT_SECRET:-$(openssl rand -64 | base64)}
JWT_TTL_SECONDS=${JWT_TTL_SECONDS:-86400}
SCANNER_RATE_LIMIT_RPS=${SCANNER_RATE_LIMIT_RPS:-100}

# Cache Configuration
CACHE_TTL_SECONDS=${CACHE_TTL_SECONDS:-300}
CACHE_MAX_SIZE=${CACHE_MAX_SIZE:-10000}

# Scanner Configuration
PDP_DEFAULT_ACTION=${PDP_DEFAULT_ACTION:-allow}
PDP_BLOCK_ON_SEVERITY=${PDP_BLOCK_ON_SEVERITY:-high,critical}
AUDIT_ENABLED=${AUDIT_ENABLED:-true}
REDACT_AUDIT_PROMPT=${REDACT_AUDIT_PROMPT:-true}
POLICY_VERSION=${POLICY_VERSION:-prod-1.0}

# Performance Configuration
MAX_PROMPT_LENGTH=${MAX_PROMPT_LENGTH:-10000}
REQUEST_TIMEOUT=${REQUEST_TIMEOUT:-30}

# Logging Configuration
LOG_LEVEL=${LOG_LEVEL:-INFO}
LOG_FORMAT=${LOG_FORMAT:-json}

# Monitoring Configuration
ENABLE_METRICS=${ENABLE_METRICS:-true}
GRAFANA_USER=${GRAFANA_USER:-admin}
GRAFANA_PASSWORD=${GRAFANA_PASSWORD:-$(openssl rand -base64 16)}

# SSL/TLS Configuration
SSL_CERT_PATH=${SSL_CERT_PATH:-/etc/ssl/certs/scanner.crt}
SSL_KEY_PATH=${SSL_KEY_PATH:-/etc/ssl/private/scanner.key}

# Docker Configuration
COMPOSE_PROJECT_NAME=llm-security-scanner
COMPOSE_FILE=docker-compose.prod.yml
```

## Docker Compose Production (docker-compose.prod.yml)

```yaml
name: llm-security-scanner-prod

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-scanner_prod}
      POSTGRES_USER: ${POSTGRES_USER:-scanner_user}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-scanner_password}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./backups:/backups
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-scanner_user} -d ${POSTGRES_DB:-scanner_prod}"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s
    resources:
      limits:
        memory: 2G
        cpus: '1.0'
      reservations:
        memory: 1G
        cpus: '0.5'

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --requirepass ${REDIS_PASSWORD:-} --maxmemory 512mb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
      - ./redis.conf:/usr/local/etc/redis/redis.conf
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD:-}", "ping"]
      interval: 30s
      timeout: 10s
      retries: 5
    resources:
      limits:
        memory: 1G
        cpus: '0.5'

  scanner:
    build:
      context: ./backend
      dockerfile: Dockerfile.prod
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      SCANNER_DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-scanner_user}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-scanner_prod}
      SCANNER_REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      SCANNER_LLM_PROVIDER_URL: ${LLM_PROVIDER_URL}
      SCANNER_API_KEYS: '["${API_KEY_DEFAULT:-}"]'
      SCANNER_JWT_SECRET: ${JWT_SECRET}
      SCANNER_JWT_TTL_SECONDS: ${JWT_TTL_SECONDS:-86400}
      SCANNER_LOG_LEVEL: ${LOG_LEVEL:-INFO}
      SCANNER_CACHE_TTL_SECONDS: ${CACHE_TTL_SECONDS:-300}
      SCANNER_DEFAULT_TENANT_ID: ${DEFAULT_TENANT_ID:-default}
      SCANNER_PDP_DEFAULT_ACTION: ${PDP_DEFAULT_ACTION:-allow}
      SCANNER_PDP_BLOCK_ON_SEVERITY: '["${PDP_BLOCK_ON_SEVERITY:-high,critical}"]'
      SCANNER_AUDIT_ENABLED: ${AUDIT_ENABLED:-true}
      SCANNER_REDACT_AUDIT_PROMPT: ${REDACT_AUDIT_PROMPT:-true}
      SCANNER_POLICY_VERSION: ${POLICY_VERSION:-prod-1.0}
      SCANNER_LLM_TIMEOUT_SECONDS: ${LLM_TIMEOUT_SECONDS:-30}
      SCANNER_RATE_LIMIT_RPS: ${SCANNER_RATE_LIMIT_RPS:-100}
      SCANNER_MAX_PROMPT_LENGTH: ${MAX_PROMPT_LENGTH:-10000}
    volumes:
      - ./backend/rules:/app/rules:ro
      - ./logs:/app/logs
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:8000/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '1.0'
        reservations:
          memory: 512M
          cpus: '0.5'

  ui:
    build:
      context: ./frontend
      dockerfile: Dockerfile.prod
    restart: unless-stopped
    depends_on:
      scanner:
        condition: service_healthy
    environment:
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-https://your-domain.com}
    ports:
      - "443:3000"
    volumes:
      - ./ssl:/etc/ssl/nginx:ro
      - ./logs/nginx:/var/log/nginx
    healthcheck:
      test: ["CMD", "curl", "-fsS", "https://localhost/"]
      interval: 30s
      timeout: 10s
      retries: 5

  prometheus:
    image: prom/prometheus:v2.53.0
    restart: unless-stopped
    volumes:
      - ./prometheus.prod.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/etc/prometheus/console_libraries'
      - '--web.console.templates=/etc/prometheus/consoles'
      - '--storage.tsdb.retention.time=200h'
      - '--web.enable-lifecycle'
    ports:
      - "9090:9090"
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '0.5'

  grafana:
    image: grafana/grafana:11.1.0
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_USER: ${GRAFANA_USER:-admin}
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-admin}
      GF_USERS_ALLOW_SIGN_UP: "false"
      GF_INSTALL_PLUGINS: "grafana-clock-panel,grafana-simple-json-datasource"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/var/lib/grafana/dashboards:ro
    ports:
      - "3001:3000"
    depends_on:
      - prometheus
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '0.5'

  nginx:
    image: nginx:alpine
    restart: unless-stopped
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
      - ./logs/nginx:/var/log/nginx
    ports:
      - "80:80"
      - "443:443"
    depends_on:
      - scanner
      - ui
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '0.5'

volumes:
  postgres_data:
  redis_data:
  prometheus_data:
  grafana_data:
```

## Nginx Configuration (nginx/nginx.conf)

```nginx
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';

    access_log /var/log/nginx/access.log main;

    # Performance optimizations
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    server_tokens off;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

    # Gzip compression
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript application/javascript application/xml+rss application/json;

    # Security headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    upstream scanner_backend {
        server scanner:8000;
        keepalive 32;
    }

    upstream frontend {
        server ui:3000;
        keepalive 32;
    }

    server {
        listen 80;
        server_name your-domain.com www.your-domain.com;

        # Redirect to HTTPS
        return 301 https://$server_name$request_uri;
    }

    server {
        listen 443 ssl http2;
        server_name your-domain.com www.your-domain.com;

        # SSL configuration
        ssl_certificate /etc/nginx/ssl/cert.pem;
        ssl_certificate_key /etc/nginx/ssl/key.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers off;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 1d;

        # HSTS
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;

        # Security
        client_max_body_size 10M;
        client_body_timeout 60s;
        client_header_timeout 60s;

        # Rate limiting
        limit_req zone=api burst=20 nodelay;
        limit_req zone=login burst=3 nodelay;

        # Health checks
        location /health {
            proxy_pass http://scanner_backend/health;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Metrics endpoint
        location /metrics {
            proxy_pass http://scanner_backend/metrics;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # API endpoints
        location / {
            proxy_pass http://scanner_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            
            # Timeouts
            proxy_connect_timeout 30s;
            proxy_send_timeout 30s;
            proxy_read_timeout 30s;
            
            # WebSocket support
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
        }

        # Frontend
        location / {
            proxy_pass http://frontend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            
            # Cache static assets
            location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
                expires 1y;
                add_header Cache-Control "public, immutable";
            }
        }
    }
}
```

## Kubernetes Configuration (k8s/)

### Deployment (k8s/deployment.yaml)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llm-security-scanner
  labels:
    app: llm-security-scanner
spec:
  replicas: 3
  selector:
    matchLabels:
      app: llm-security-scanner
  template:
    metadata:
      labels:
        app: llm-security-scanner
    spec:
      containers:
      - name: scanner
        image: llm-security-scanner:latest
        ports:
        - containerPort: 8000
        env:
        - name: SCANNER_DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: scanner-secrets
              key: database-url
        - name: SCANNER_REDIS_URL
          valueFrom:
            secretKeyRef:
              name: scanner-secrets
              key: redis-url
        - name: SCANNER_LLM_PROVIDER_URL
          value: "https://api.openai.com/v1/chat/completions"
        - name: SCANNER_API_KEYS
          value: "prod-api-key-12345"
        - name: SCANNER_JWT_SECRET
          valueFrom:
            secretKeyRef:
              name: scanner-secrets
              key: jwt-secret
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
```

### Service (k8s/service.yaml)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: llm-security-scanner
spec:
  selector:
    app: llm-security-scanner
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8000
  type: ClusterIP
```

### Ingress (k8s/ingress.yaml)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: llm-security-scanner
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
  - hosts:
    - scanner.your-domain.com
    secretName: scanner-tls
  rules:
  - host: scanner.your-domain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: llm-security-scanner
            port:
              number: 80
```

## Monitoring and Logging Configuration

### Prometheus Configuration (prometheus.prod.yml)

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules/*.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

scrape_configs:
  - job_name: 'llm-security-scanner'
    static_configs:
      - targets: ['scanner:8000']
    metrics_path: '/metrics'
    scrape_interval: 15s
    scrape_timeout: 10s

  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'grafana'
    static_configs:
      - targets: ['grafana:3000']
```

### Grafana Dashboard Configuration

Create dashboards for:
- Request metrics (RPS, latency, error rates)
- Cache performance (hit/miss rates, memory usage)
- Rule effectiveness (top matched rules, block rates)
- System health (CPU, memory, disk usage)

## Backup Strategy

### Database Backup

```bash
# Daily backup
docker exec postgres pg_dump -U scanner scanner > backup_$(date +%Y%m%d).sql

# Upload to S3
aws s3 cp backup_$(date +%Y%m%d).sql s3://your-backup-bucket/scanner/

# Retain 30 days
find ./ -name "backup_*.sql" -mtime +30 -delete
```

### Redis Backup

```bash
# Daily RDB backup
docker exec redis redis-cli --rdb /data/dump_$(date +%Y%m%d).rdb

# Upload to S3
aws s3 cp /data/dump_$(date +%Y%m%d).rdb s3://your-backup-bucket/redis/
```

## Security Hardening

### Network Security

```yaml
# Network policies for Kubernetes
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: scanner-network-policy
spec:
  podSelector:
    matchLabels:
      app: llm-security-scanner
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector: {}
    podSelector: {}
    ports:
    - protocol: TCP
      port: 8000
  egress:
  - to:
    - namespaceSelector: {}
      podSelector: {}
    ports:
    - protocol: TCP
      port: 5432  # PostgreSQL
    - protocol: TCP
      port: 6379  # Redis
    - protocol: TCP
      port: 443   # OpenAI API
```

### Container Security

```bash
# Scan container images for vulnerabilities
docker scan llm-security-scanner:latest

# Run as non-root user
docker run --user=1000:1000 llm-security-scanner:latest

# Read-only root filesystem
docker run --read-only --tmpfs=/tmp llm-security-scanner:latest
```

These configurations provide a solid foundation for production deployment. Adjust based on your specific requirements and infrastructure.