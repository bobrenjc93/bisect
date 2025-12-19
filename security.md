# Security Guide for GitHub Bisect Bot

This document describes security considerations, best practices, and configurations for deploying the GitHub Bisect Bot securely.

## Table of Contents

- [Threat Model](#threat-model)
- [Authentication & Authorization](#authentication--authorization)
- [Input Validation](#input-validation)
- [Container Security](#container-security)
- [Secrets Management](#secrets-management)
- [Network Security](#network-security)
- [Rate Limiting](#rate-limiting)
- [Logging & Monitoring](#logging--monitoring)
- [Hardening Checklist](#hardening-checklist)

## Threat Model

The GitHub Bisect Bot has unique security considerations due to its design:

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                              TRUST BOUNDARIES                                   │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│  ┌─────────────┐                                                               │
│  │   GitHub    │◀──── Untrusted: User-provided commands & commit SHAs          │
│  │  Webhooks   │                                                               │
│  └──────┬──────┘                                                               │
│         │                                                                      │
│         ▼                                                                      │
│  ┌─────────────┐                                                              │
│  │   Bot API   │─────────────────────────────────────────┐                    │
│  │  (FastAPI)  │                                         │                    │
│  └──────┬──────┘                                         ▼                    │
│         │                                         ┌─────────────┐             │
│         │                                         │   Docker    │◀── Sandboxed│
│         │                                         │  Container  │   Execution │
│         ▼                                         └─────────────┘             │
│  ┌─────────────┐                                                              │
│  │  PostgreSQL │◀──── Supabase (job state, results)                           │
│  │  (Supabase) │                                                              │
│  └─────────────┘                                                              │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

### Key Threats

| Threat | Impact | Mitigation |
|--------|--------|------------|
| **Command Injection** | Remote code execution in containers | Input validation, sandboxed containers |
| **Webhook Spoofing** | Unauthorized job execution | HMAC signature verification |
| **Token Theft** | GitHub account compromise | Encrypted storage, minimal token scope |
| **Container Escape** | Host system compromise | Rootless containers, read-only filesystem |
| **Denial of Service** | Service unavailability | Rate limiting, resource limits |
| **Data Exfiltration** | Repository code theft | Network isolation, no outbound from containers |

## Authentication & Authorization

### GitHub App Authentication

The bot authenticates to GitHub using a GitHub App with:
- **App-level authentication**: JWT signed with the private key
- **Installation-level access tokens**: Short-lived tokens (1 hour) for specific installations

```python
# JWT tokens are generated fresh and expire in 10 minutes
payload = {
    "iat": now - 60,  # Account for clock drift
    "exp": now + 600,  # 10-minute expiration
    "iss": app_id,
}
```

### Webhook Verification

All incoming webhooks are verified using HMAC-SHA256:

```python
def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

**Security Note**: The signature comparison uses `hmac.compare_digest()` to prevent timing attacks.

### Required Permissions

Configure your GitHub App with **minimal permissions**:

| Permission | Access | Reason |
|------------|--------|--------|
| Issues | Read & Write | Read commands, post results |
| Contents | Read-only | Clone repositories |
| Metadata | Read-only | Required for all apps |

## Input Validation

### SHA Validation

All commit SHAs are validated to be valid hex strings:

```python
import re

SHA_PATTERN = re.compile(r'^[a-fA-F0-9]{7,40}$')

def validate_sha(sha: str) -> bool:
    """Validate a commit SHA is a valid hex string."""
    return bool(SHA_PATTERN.match(sha))
```

### Command Validation

Test commands are validated and sanitized:

```python
# Dangerous patterns that are blocked
DANGEROUS_PATTERNS = [
    r';\s*rm\s+-rf',          # Destructive commands
    r'\$\(',                   # Command substitution
    r'`',                      # Backtick execution
    r'\|.*sh\s*$',             # Piping to shell
    r'>\s*/etc/',              # Writing to system directories
    r'curl.*\|.*sh',           # Remote code execution patterns
    r'wget.*\|.*sh',
    r'\\x[0-9a-fA-F]{2}',      # Hex-encoded payloads
]

def validate_test_command(command: str) -> tuple[bool, str | None]:
    """
    Validate a test command for security.
    Returns (is_valid, error_message).
    """
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Command contains disallowed pattern"
    return True, None
```

### Repository Validation

Repository names are validated against GitHub's naming rules:

```python
REPO_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9._-]+$')
OWNER_PATTERN = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$')

def validate_repo_path(owner: str, repo: str) -> bool:
    """Validate repository owner and name."""
    return (
        bool(OWNER_PATTERN.match(owner)) and
        bool(REPO_NAME_PATTERN.match(repo)) and
        len(owner) <= 39 and
        len(repo) <= 100
    )
```

## Container Security

### Sandboxed Execution

Test commands run inside Docker containers with strict security controls:

```python
container_output = docker_client.containers.run(
    image=runner_image,
    command=[...],
    remove=True,                    # Auto-cleanup
    network_mode="none",            # No network access
    mem_limit="2g",                 # Memory limit
    cpu_period=100000,
    cpu_quota=100000,               # 1 CPU limit
    pids_limit=256,                 # Process limit
    read_only=True,                 # Read-only root filesystem
    tmpfs={'/tmp': 'size=512m'},    # Writable temp space
    security_opt=["no-new-privileges:true"],
    cap_drop=["ALL"],               # Drop all capabilities
    user="1000:1000",               # Non-root user
)
```

### Container Image Security

The runner container is hardened:

```dockerfile
# Use specific version, not :latest
FROM python:3.12-slim-bookworm

# Create non-root user
RUN groupadd -r bisect && useradd -r -g bisect bisect

# Remove unnecessary packages
RUN apt-get purge -y --auto-remove \
    && rm -rf /var/lib/apt/lists/*

# Set ownership
RUN chown -R bisect:bisect /workspace

# Run as non-root
USER bisect:bisect
```

### Docker Socket Security

⚠️ **Warning**: Mounting the Docker socket (`/var/run/docker.sock`) grants near-root access to the host.

For production, consider these alternatives:

1. **Docker-in-Docker (dind)** - Run Docker daemon inside a container
2. **Podman** - Rootless container runtime
3. **gVisor** - Additional sandboxing layer
4. **Kubernetes** - Use `securityContext` and Pod Security Standards

## Secrets Management

### Environment Variables

Required secrets are loaded from environment variables:

| Secret | Purpose | Rotation Period |
|--------|---------|-----------------|
| `GITHUB_PRIVATE_KEY_PATH` | GitHub App authentication | Yearly |
| `GITHUB_WEBHOOK_SECRET` | Webhook verification | When compromised |
| `DATABASE_URL` | Database connection (Supabase) | When compromised |

### Database Encryption

Sensitive fields in the database are encrypted at rest:

```python
from cryptography.fernet import Fernet

class EncryptedField:
    """Encrypted database field using Fernet symmetric encryption."""
    
    def __init__(self):
        key = os.environ.get("ENCRYPTION_KEY")
        if not key:
            raise ValueError("ENCRYPTION_KEY environment variable required")
        self.fernet = Fernet(key.encode())
    
    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode()).decode()
    
    def decrypt(self, value: str) -> str:
        return self.fernet.decrypt(value.encode()).decode()
```

### Key Rotation

To rotate the encryption key:

```bash
# Generate new key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set both keys during transition
export OLD_ENCRYPTION_KEY="..."
export ENCRYPTION_KEY="..."

# Run migration script to re-encrypt data
python -m app.migrate_encryption
```

### GitHub Token Security

- Installation tokens are cached for a maximum of 50 minutes (tokens expire after 1 hour)
- Tokens are never logged or exposed in error messages
- Clone URLs with embedded tokens are used only in memory

## Network Security

### Service Isolation

```yaml
# docker-compose.yml - Production network configuration
services:
  bot:
    networks:
      - frontend   # Exposed to internet (via reverse proxy)
      - backend    # Internal services only

  # No Redis - jobs stored in PostgreSQL (Supabase)

networks:
  frontend:
  backend:
    internal: true
  docker:
    internal: true
```

### Container Network Isolation

Bisect containers run with `network_mode: "none"`:

```python
# No network access - containers can only:
# 1. Read the cloned repository (already on disk)
# 2. Run the test command
# 3. Write results to stdout
container = docker_client.containers.run(
    network_mode="none",
    # ...
)
```

### TLS/SSL Configuration

For production, always use TLS:

```nginx
# nginx.conf
server {
    listen 443 ssl http2;
    
    ssl_certificate /etc/ssl/certs/cert.pem;
    ssl_certificate_key /etc/ssl/private/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;
    
    location / {
        proxy_pass http://bot:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Rate Limiting

### API Rate Limits

The API implements rate limiting to prevent abuse:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/webhook")
@limiter.limit("100/minute")  # 100 requests per minute per IP
async def webhook(request: Request):
    ...
```

### Per-Repository Limits

Job limits are enforced per repository:

| Tier | Jobs/Month | Max Duration | Concurrent Jobs |
|------|------------|--------------|-----------------|
| Free | 50 | 30 minutes | 1 |
| Pro | 500 | 60 minutes | 3 |
| Enterprise | Unlimited | 120 minutes | 10 |

### Queue Protection

Job queue uses PostgreSQL with built-in protections:

```python
# Maximum pending jobs to prevent resource exhaustion
MAX_PENDING_JOBS = 1000

async def create_job(job_data):
    pending_count = await db.query(BisectJob).filter(
        BisectJob.status == JobStatus.PENDING
    ).count()
    if pending_count > MAX_PENDING_JOBS:
        raise QueueFullError("Job queue is full, try again later")
    return await db.add(job_data)
```

## Logging & Monitoring

### Secure Logging

Logs are sanitized to prevent secret exposure:

```python
import re

SECRET_PATTERNS = [
    (r'(ghp_[a-zA-Z0-9]{36})', '[GITHUB_TOKEN]'),
    (r'(ghs_[a-zA-Z0-9]{36})', '[GITHUB_TOKEN]'),
    (r'(x-access-token:)[^@]+(@)', r'\1[REDACTED]\2'),
    (r'(password[=:])\S+', r'\1[REDACTED]'),
    (r'(secret[=:])\S+', r'\1[REDACTED]'),
]

class SecureFormatter(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        for pattern, replacement in SECRET_PATTERNS:
            message = re.sub(pattern, replacement, message, flags=re.IGNORECASE)
        return message
```

### Audit Logging

Security-relevant events are logged:

```python
# Log all bisect job requests
logger.info(
    "bisect_job_created",
    extra={
        "owner": owner,
        "repo": repo,
        "issue": issue_number,
        "requester": comment_author,
        "ip": request.client.host,
    }
)
```

### Metrics & Alerting

Monitor these security metrics:

| Metric | Alert Threshold |
|--------|-----------------|
| Webhook signature failures | > 10/minute |
| Rate limit hits | > 100/minute |
| Failed jobs | > 50% failure rate |
| Container timeouts | > 20% timeout rate |
| Queue depth | > 500 jobs |

## Security Headers

The API sets security headers on all responses:

```python
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["your-domain.com", "*.your-domain.com"]
)
```

## Hardening Checklist

### Pre-Deployment

- [ ] GitHub App has minimal permissions configured
- [ ] Webhook secret is a strong random value (32+ characters)
- [ ] Private key is stored securely (not in git)
- [ ] Database uses SSL/TLS connection
- [ ] Database connection uses SSL/TLS
- [ ] All secrets are in environment variables, not config files
- [ ] Encryption key is generated and set

### Docker Security

- [ ] Runner container runs as non-root user
- [ ] Container has `read_only: true` and limited tmpfs
- [ ] Network mode is "none" for bisect containers
- [ ] All capabilities are dropped (`cap_drop: ALL`)
- [ ] `no-new-privileges` security option is set
- [ ] Resource limits (CPU, memory, pids) are configured
- [ ] Docker socket access is minimized or eliminated

### Network Security

- [ ] API is behind a reverse proxy with TLS
- [ ] Internal services are on isolated networks
- [ ] PostgreSQL (Supabase) uses secure connection pooling
- [ ] Firewall rules block unnecessary traffic

### Monitoring

- [ ] Logging is configured with secret redaction
- [ ] Audit logs capture security events
- [ ] Alerts are set for security metrics
- [ ] Log aggregation is in place

### Ongoing

- [ ] Dependencies are regularly updated
- [ ] Security patches are applied promptly
- [ ] Access logs are reviewed periodically
- [ ] Secrets are rotated according to schedule
- [ ] Container images are rebuilt with security updates

## Incident Response

### Suspected Compromise

1. **Rotate all secrets immediately**
   - GitHub App private key (regenerate in GitHub)
   - Webhook secret
   - Database credentials
   - Encryption keys

2. **Revoke active tokens**
   ```bash
   # Revoke all installation tokens by regenerating the private key
   ```

3. **Review audit logs**
   ```bash
   # Search for unusual activity
   grep -E "(failed|error|unauthorized)" /var/log/bisect/*.log
   ```

4. **Check for unauthorized jobs**
   ```sql
   SELECT * FROM bisect_jobs 
   WHERE created_at > NOW() - INTERVAL '24 hours'
   ORDER BY created_at DESC;
   ```

### Reporting Vulnerabilities

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public issue
2. Email security@your-domain.com with details
3. Include steps to reproduce if possible
4. We aim to respond within 48 hours

## Additional Resources

- [GitHub App Security Best Practices](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/best-practices-for-creating-a-github-app)
- [Docker Security](https://docs.docker.com/engine/security/)
- [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/)
- [CIS Docker Benchmark](https://www.cisecurity.org/benchmark/docker)

