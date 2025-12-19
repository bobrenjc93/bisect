# Productionization Guide

This guide covers everything needed to deploy the GitHub Bisect Bot to production, including account setup, infrastructure provisioning on Hetzner, and building a web application for user registration.

## Table of Contents

1. [Accounts and Services to Register](#1-accounts-and-services-to-register)
2. [GitHub App Setup](#2-github-app-setup)
3. [Hetzner Infrastructure](#3-hetzner-infrastructure)
4. [Web Application Architecture](#4-web-application-architecture)
5. [Database Schema](#5-database-schema)
6. [User Registration Flow](#6-user-registration-flow)
7. [Deployment](#7-deployment)
8. [Security Checklist](#8-security-checklist)

---

## 1. Accounts and Services to Register

### Required Accounts

| Service | Purpose | Estimated Cost |
|---------|---------|----------------|
| **GitHub App** | Webhook integration, OAuth login, API access | Free |
| **Hetzner Cloud** | VPS hosting | ~€5-15/month |
| **Supabase** | PostgreSQL database hosting | Free tier / $25/mo Pro |
| **Domain Registrar** | Custom domain (e.g., Namecheap, Cloudflare, Porkbun) | ~$10-15/year |

### Optional Services

| Service | Purpose | Estimated Cost |
|---------|---------|----------------|
| **Email Provider** | Transactional emails (Resend, Postmark, SendGrid) | Free tier available |
| **Sentry** | Error tracking | Free tier available |
| **Better Uptime / Uptime Robot** | Uptime monitoring | Free tier available |

### Database Provider: Supabase (Recommended)

This guide uses [**Supabase**](https://supabase.com) for PostgreSQL hosting. Supabase provides a managed PostgreSQL database with excellent developer experience, automatic backups, and a generous free tier.

#### Why Supabase?

| Feature | Benefit |
|---------|---------|
| **Free tier** | 500 MB storage, 2 GB bandwidth - enough for development and small production |
| **Automatic backups** | Daily backups with point-in-time recovery (Pro plan) |
| **Dashboard** | Visual SQL editor, table viewer, real-time logs |
| **Connection pooling** | Built-in PgBouncer for efficient connections |
| **Row Level Security** | Fine-grained access control (optional) |
| **EU regions** | Frankfurt datacenter for low latency to Hetzner |

#### Supabase Pricing

| Plan | Storage | Price | Best For |
|------|---------|-------|----------|
| **Free** | 500 MB | $0/mo | Development, testing |
| **Pro** | 8 GB | $25/mo | Production workloads |
| **Team** | 8 GB | $599/mo | Teams with compliance needs |

#### Supabase Setup

1. **Create an account** at [supabase.com](https://supabase.com)

2. **Create a new project**:
   - Click "New Project"
   - **Name**: `bisect-bot`
   - **Database Password**: Generate a strong password (save this!)
   - **Region**: Choose `eu-central-1` (Frankfurt) for lowest latency to Hetzner
   - Click "Create new project"

3. **Wait for provisioning** (~2 minutes)

4. **Get your connection string**:
   - Go to **Project Settings** → **Database**
   - Under "Connection string", select **URI**
   - Copy the connection string (looks like `postgresql://postgres:[YOUR-PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres`)

5. **Enable connection pooling** (recommended for production):
   - In the same Database settings page
   - Under "Connection pooling", copy the **Pooler connection string**
   - Use port `6543` for transaction mode (recommended)

Your connection strings:

```bash
# Direct connection (for migrations)
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres

# Pooled connection (for application - recommended)
DATABASE_URL=postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:6543/postgres
```

#### Alternative Providers

If you prefer a different provider:

| Provider | Free Tier | Paid Starting | Notes |
|----------|-----------|---------------|-------|
| [**Neon**](https://neon.tech) | 0.5 GB | $19/mo | Serverless, branching |
| [**Railway**](https://railway.app) | $5 credit | Usage-based | Simple setup |
| [**Hetzner Managed**](https://www.hetzner.com/cloud/managed-database) | None | €12/mo | Same network as VPS |

---

## 2. GitHub App Setup

You'll need to create **two** GitHub integrations:
1. **GitHub App** - For webhook handling and repository access (cloning, posting comments)
2. **GitHub OAuth App** - For user authentication in the web UI

### 2.1 Create the GitHub App

The GitHub App handles webhook events and interacts with repositories.

#### Step-by-Step Instructions

1. **Navigate to GitHub App creation page**:
   - Go to [https://github.com/settings/apps/new](https://github.com/settings/apps/new)
   - Or: GitHub → Profile Picture → Settings → Developer settings → GitHub Apps → New GitHub App

2. **Fill in basic information**:

   | Field | Value | Notes |
   |-------|-------|-------|
   | **GitHub App name** | `Bisect Bot` | Must be globally unique across all of GitHub |
   | **Homepage URL** | `https://your-domain.com` | Your production URL |
   | **Callback URL** | `https://your-domain.com/auth/callback` | OAuth callback for user auth |
   | **Setup URL** (optional) | `https://your-domain.com/setup` | Shown after installation |
   | **Webhook URL** | `https://your-domain.com/webhook` | Receives GitHub events |
   | **Webhook secret** | Generate with `openssl rand -hex 32` | **Save this value!** |

3. **Set Repository permissions**:

   | Permission | Access Level | Reason |
   |------------|--------------|--------|
   | **Contents** | Read-only | Clone repositories for bisection |
   | **Issues** | Read & write | Post comments with results |
   | **Metadata** | Read-only | Access repository information |

4. **Set Account permissions**:

   | Permission | Access Level | Reason |
   |------------|--------------|--------|
   | **Email addresses** | Read-only | User identification (optional) |

5. **Subscribe to events** (check these boxes):
   - ✅ **Issue comment** - Triggers bisect on `/bisect` command
   - ✅ **Installation** - Tracks when app is installed/removed
   - ✅ **Installation repositories** - Tracks repo changes

6. **Where can this GitHub App be installed?**
   - Select **"Any account"** for public use
   - Select **"Only on this account"** for private/internal use

7. **Click "Create GitHub App"**

### 2.2 Find Your App ID

After creating the app, you'll be redirected to the app's settings page.

**Locating the App ID:**

1. Look at the top of the settings page, just below the app name
2. You'll see: **"App ID: 123456"** (your number will be different)
3. **Copy this number** - this is your `GITHUB_APP_ID`

**Screenshot location:**
```
┌─────────────────────────────────────────────────┐
│ ← Settings / Developer settings / GitHub Apps  │
├─────────────────────────────────────────────────┤
│                                                 │
│  🤖 Bisect Bot                                  │
│  App ID: 123456  ← ─── THIS IS YOUR APP ID     │
│                                                 │
│  About                                          │
│  ┌──────────────────────────────────────────┐  │
│  │ Owned by: @your-username                 │  │
│  │ Public link: github.com/apps/bisect-bot  │  │
│  └──────────────────────────────────────────┘  │
```

**Also note the App Slug** (URL-friendly name):
- Look at the URL: `https://github.com/settings/apps/bisect-bot`
- The slug is `bisect-bot` (used for installation URLs)

### 2.3 Generate a Private Key

The private key is used to authenticate as the GitHub App.

1. Scroll down to the **"Private keys"** section
2. Click **"Generate a private key"**
3. A `.pem` file will download automatically (e.g., `bisect-bot.2024-01-15.private-key.pem`)
4. **Store this file securely** - you'll need it for deployment

```bash
# On your production server
mkdir -p /opt/bisect-bot/secrets
chmod 700 /opt/bisect-bot/secrets
mv your-app.private-key.pem /opt/bisect-bot/secrets/private-key.pem
chmod 600 /opt/bisect-bot/secrets/private-key.pem
```

### 2.4 Create a GitHub OAuth App

The OAuth App is used for user authentication in the web UI (separate from the GitHub App).

#### Step-by-Step Instructions

1. **Navigate to OAuth App creation**:
   - Go to [https://github.com/settings/developers](https://github.com/settings/developers)
   - Or: GitHub → Profile Picture → Settings → Developer settings → OAuth Apps
   - Click **"New OAuth App"**

2. **Fill in application details**:

   | Field | Value |
   |-------|-------|
   | **Application name** | `Bisect Bot` |
   | **Homepage URL** | `https://your-domain.com` |
   | **Authorization callback URL** | `https://your-domain.com/auth/callback` |

   > ⚠️ **Important**: The callback URL must match **exactly** - including the protocol (`https://`) and path (`/auth/callback`)

3. **Click "Register application"**

4. **Get your credentials**:
   - **Client ID**: Displayed on the app page (e.g., `Iv1.abc123def456ghi7`)
   - **Client Secret**: Click "Generate a new client secret", then **copy immediately** (shown only once!)

### 2.5 Credentials Summary

After completing all steps, you should have these credentials:

```bash
# GitHub App (for webhooks and repo access)
GITHUB_APP_ID=123456                              # From app settings page
GITHUB_WEBHOOK_SECRET=your-32-char-secret-here    # Generated when creating app
GITHUB_PRIVATE_KEY_PATH=/app/secrets/private-key.pem  # Downloaded .pem file

# GitHub OAuth App (for UI authentication)
GITHUB_CLIENT_ID=Iv1.abc123def456ghi7             # From OAuth app page
GITHUB_CLIENT_SECRET=your_client_secret_here      # Generated on OAuth app page

# Additional settings
BASE_URL=https://your-domain.com                  # Your production URL
SESSION_SECRET=generate-random-64-char-string     # For session cookies
```

**Generate a session secret:**
```bash
openssl rand -hex 32
```

### 2.6 Verification Checklist

Before proceeding, verify you have:

- [ ] GitHub App ID (6-digit number)
- [ ] GitHub App webhook secret (32+ characters)
- [ ] GitHub App private key file (`.pem`)
- [ ] GitHub OAuth Client ID (starts with `Iv1.`)
- [ ] GitHub OAuth Client Secret
- [ ] Session secret generated

---

## 3. Hetzner Infrastructure

### 3.1 Create Hetzner Cloud Account

1. Go to [Hetzner Cloud](https://console.hetzner.cloud/)
2. Sign up and verify your account
3. Create a new project (e.g., "bisect-bot")

### 3.2 Provision a Server

#### Recommended Server Specs

| Tier | Server Type | vCPU | RAM | Storage | Cost | Use Case |
|------|-------------|------|-----|---------|------|----------|
| **Starter** | CX22 | 2 | 4 GB | 40 GB | ~€4/mo | Low traffic, testing |
| **Recommended** | CX32 | 4 | 8 GB | 80 GB | ~€8/mo | Production workloads |
| **High Traffic** | CX42 | 8 | 16 GB | 160 GB | ~€16/mo | Heavy bisect usage |

#### Create the Server

1. Click **Add Server**
2. **Location**: Choose closest to your users (e.g., `fsn1` Falkenstein)
3. **Image**: Ubuntu 24.04
4. **Type**: CX32 (recommended)
5. **Networking**: 
   - Enable IPv4 (required)
   - Enable IPv6 (recommended)
6. **SSH Keys**: Add your public SSH key
7. **Name**: `bisect-bot-prod`
8. Click **Create & Buy Now**

### 3.3 Initial Server Setup

SSH into your new server:

```bash
ssh root@YOUR_SERVER_IP
```

#### Update System and Install Dependencies

```bash
# Update system
apt update && apt upgrade -y

# Install required packages
apt install -y \
    curl \
    git \
    ufw \
    fail2ban

# Install Docker
curl -fsSL https://get.docker.com | sh

# Install Docker Compose
apt install -y docker-compose-plugin

# Add your user (if not using root)
adduser deploy
usermod -aG docker deploy
usermod -aG sudo deploy
```

#### Configure Firewall

```bash
# Enable UFW
ufw default deny incoming
ufw default allow outgoing

# Allow SSH
ufw allow 22/tcp

# Allow HTTP and HTTPS
ufw allow 80/tcp
ufw allow 443/tcp

# Enable firewall
ufw enable

# Verify status
ufw status
```

#### Configure Fail2Ban

```bash
# Start and enable fail2ban
systemctl enable fail2ban
systemctl start fail2ban
```

### 3.4 DNS Configuration

#### Option A: Using Hetzner DNS

1. In Hetzner Cloud Console, go to **DNS**
2. Add your domain
3. Create records:

   | Type | Name | Value | TTL |
   |------|------|-------|-----|
   | A | @ | YOUR_SERVER_IP | 300 |
   | A | www | YOUR_SERVER_IP | 300 |
   | AAAA | @ | YOUR_SERVER_IPV6 | 300 |
   | AAAA | www | YOUR_SERVER_IPV6 | 300 |

4. Update nameservers at your domain registrar to:
   - `helium.ns.hetzner.de`
   - `hydrogen.ns.hetzner.de`
   - `oxygen.ns.hetzner.de`

#### Option B: Using Cloudflare (Recommended)

1. Add your domain to Cloudflare
2. Create A/AAAA records pointing to your server
3. Enable proxy (orange cloud) for DDoS protection
4. Set SSL mode to "Full (Strict)"

### 3.5 SSL with Caddy

Caddy handles SSL certificates automatically. See the deployment section for the Caddy configuration.

---

## 4. Web Application Architecture

### 4.1 System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Hetzner VPS                              │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    Docker Network                         │   │
│  │                                                           │   │
│  │   ┌─────────┐    ┌─────────────┐    ┌───────────────┐   │   │
│  │   │  Caddy  │───▶│   Web App   │───▶│  PostgreSQL   │   │   │
│  │   │  :80    │    │   :8000     │    │    :5432      │   │   │
│  │   │  :443   │    └─────────────┘    └───────────────┘   │   │
│  │   └─────────┘           │                               │   │
│  │        │                │                               │   │
│  │        │          ┌─────▼─────┐                         │   │
│  │        └─────────▶│ Bisect Bot│                         │   │
│  │                   │   :8001   │                         │   │
│  │                   └───────────┘                         │   │
│  │                         │                               │   │
│  │                   ┌─────▼─────┐                         │   │
│  │                   │  Runner   │ (spawned per job)       │   │
│  │                   │ Container │                         │   │
│  │                   └───────────┘                         │   │
│  └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 Components

| Component | Purpose | Technology |
|-----------|---------|------------|
| **Caddy** | Reverse proxy, automatic HTTPS | Caddy 2.x |
| **Web App** | User dashboard, OAuth, repo management | FastAPI + Jinja2 or React |
| **Bisect Bot** | Webhook handler, job execution | FastAPI (existing) |
| **PostgreSQL** | Persistent storage | PostgreSQL 16 |
| **Runner** | Isolated bisect execution | Docker containers |

### 4.3 URL Routing

| Path | Destination | Purpose |
|------|-------------|---------|
| `/` | Web App | Landing page |
| `/dashboard` | Web App | User dashboard |
| `/auth/*` | Web App | OAuth flow |
| `/api/*` | Web App | Dashboard API |
| `/webhook` | Bisect Bot | GitHub webhooks |
| `/health` | Bisect Bot | Health checks |

---

## 5. Database Schema and Migrations

The database schema is managed using **Alembic** migrations. This ensures:
- Version-controlled schema changes
- Safe, repeatable deployments
- Easy rollbacks if needed
- No manual SQL execution required

### 5.1 Schema Overview

The database consists of these tables (managed via `app/models.py`):

| Table | Purpose |
|-------|---------|
| `users` | GitHub users authenticated via OAuth |
| `installations` | GitHub App installations on accounts |
| `repositories` | Repositories where the app is installed |
| `bisect_jobs` | Bisect job history and results |
| `usage_stats` | Usage tracking for rate limiting |
| `rate_limits` | Rate limit configuration by tier |

### 5.2 Models Location

All SQLAlchemy models are defined in `app/models.py`. Key models:

```python
# app/models.py
class User(Base):
    """GitHub user who has authenticated via OAuth."""
    __tablename__ = "users"
    github_id = Column(BigInteger, unique=True, nullable=False)
    github_login = Column(String(255), nullable=False)
    # ...

class BisectJob(Base):
    """A bisect job requested by a user."""
    __tablename__ = "bisect_jobs"
    status = Column(Enum(JobStatus), default=JobStatus.PENDING)
    good_sha = Column(String(40), nullable=False)
    bad_sha = Column(String(40), nullable=False)
    # ...
```

### 5.3 Running Migrations

#### Initial Setup (First Deployment)

Run the initial migration to create all tables:

```bash
# Set your Supabase connection string (use direct connection, not pooled)
export DATABASE_URL="postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres"

# Run all migrations
alembic upgrade head
```

#### Checking Migration Status

```bash
# See current migration version
alembic current

# See migration history
alembic history
```

#### Creating New Migrations

When you modify `app/models.py`, generate a new migration:

```bash
# Auto-generate migration from model changes
alembic revision --autogenerate -m "Add new_column to users"

# Review the generated migration in alembic/versions/
# Then apply it
alembic upgrade head
```

#### Rolling Back

```bash
# Rollback one migration
alembic downgrade -1

# Rollback to a specific revision
alembic downgrade 001_initial

# Rollback all migrations (dangerous!)
alembic downgrade base
```

### 5.4 Migration Files

Migrations are stored in `alembic/versions/`. The initial migration includes:

- All tables with proper indexes
- Foreign key relationships with cascading deletes
- Default rate limit tiers (free, pro, enterprise)

```
alembic/
├── env.py              # Alembic environment config
├── script.py.mako      # Migration template
└── versions/
    └── 20241218_000000_initial_schema.py  # Initial migration
```

### 5.5 Default Rate Limits

The initial migration seeds default rate limits:

| Tier | Jobs/Month | Max Duration | Concurrent |
|------|------------|--------------|------------|
| `free` | 50 | 30 min | 1 |
| `pro` | 500 | 60 min | 3 |
| `enterprise` | Unlimited | 120 min | 10 |

---

## 6. User Registration Flow

### 6.1 Flow Diagram

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Landing    │     │    GitHub    │     │   Dashboard  │
│    Page      │────▶│    OAuth     │────▶│              │
└──────────────┘     └──────────────┘     └──────────────┘
                                                 │
                                                 ▼
                                          ┌──────────────┐
                                          │   Install    │
                                          │   App on     │
                                          │    Repos     │
                                          └──────────────┘
                                                 │
                                                 ▼
                                          ┌──────────────┐
                                          │  App Ready   │
                                          │  Use /bisect │
                                          └──────────────┘
```

### 6.2 Step-by-Step Implementation

#### Step 1: Landing Page

Create a landing page at `/` with:
- Product description and features
- "Sign in with GitHub" button
- Documentation links

#### Step 2: GitHub OAuth Login

**Initiate OAuth** (`GET /auth/github/login`):

```python
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
import secrets

router = APIRouter(prefix="/auth/github")

@router.get("/login")
async def github_login(request: Request):
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": f"{settings.base_url}/auth/github/callback",
        "scope": "read:user user:email",
        "state": state,
    }
    url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    return RedirectResponse(url)
```

**Handle Callback** (`GET /auth/github/callback`):

```python
@router.get("/callback")
async def github_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    # Verify state
    if state != request.session.get("oauth_state"):
        raise HTTPException(400, "Invalid state")
    
    # Exchange code for access token
    token_response = httpx.post(
        "https://github.com/login/oauth/access_token",
        data={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
        },
        headers={"Accept": "application/json"},
    )
    access_token = token_response.json()["access_token"]
    
    # Get user info
    user_response = httpx.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    github_user = user_response.json()
    
    # Create or update user in database
    user = get_or_create_user(db, github_user, access_token)
    
    # Set session
    request.session["user_id"] = user.id
    
    return RedirectResponse("/dashboard")
```

#### Step 3: Dashboard

The dashboard shows:
- User's installed repositories
- Recent bisect jobs and their status
- "Install on more repositories" button
- Usage statistics

#### Step 4: App Installation

When user clicks "Install on more repositories":

```python
@router.get("/install")
async def install_app():
    # Redirect to GitHub App installation page
    url = f"https://github.com/apps/{settings.github_app_slug}/installations/new"
    return RedirectResponse(url)
```

#### Step 5: Handle Installation Webhook

When a user installs the app, GitHub sends a webhook:

```python
@app.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    verify_signature(payload, request.headers.get("X-Hub-Signature-256"))
    
    event_type = request.headers.get("X-GitHub-Event")
    data = await request.json()
    
    if event_type == "installation":
        action = data["action"]
        installation = data["installation"]
        
        if action == "created":
            # New installation - save to database
            db_installation = Installation(
                installation_id=installation["id"],
                account_type=installation["account"]["type"],
                account_login=installation["account"]["login"],
                account_id=installation["account"]["id"],
            )
            db.add(db_installation)
            
            # Save repositories
            for repo in data.get("repositories", []):
                db_repo = Repository(
                    github_id=repo["id"],
                    installation=db_installation,
                    owner=installation["account"]["login"],
                    name=repo["name"],
                    full_name=repo["full_name"],
                    private=repo["private"],
                )
                db.add(db_repo)
            
            db.commit()
        
        elif action == "deleted":
            # Installation removed - clean up
            db.query(Installation).filter(
                Installation.installation_id == installation["id"]
            ).delete()
            db.commit()
    
    elif event_type == "installation_repositories":
        # Repositories added/removed from installation
        handle_repository_changes(db, data)
    
    return {"status": "ok"}
```

#### Step 6: User Can Now Use /bisect

Once installed, users can comment `/bisect good_sha bad_sha test_command` on any issue in their repositories.

---

## 7. Deployment

### 7.1 Project Structure

```
/opt/bisect-bot/
├── docker-compose.prod.yml
├── Caddyfile
├── .env
├── secrets/
│   └── private-key.pem
├── app/                    # Existing bot code
├── web/                    # New web app code
└── docker/                 # Runner Dockerfile
```

### 7.2 Production Docker Compose

Create `docker-compose.prod.yml`:

```yaml
version: "3.8"

services:
  # Reverse proxy with automatic HTTPS
  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - web
      - bot
    restart: unless-stopped

  # Web application (user dashboard)
  web:
    build:
      context: .
      dockerfile: Dockerfile.web
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - GITHUB_CLIENT_ID=${GITHUB_CLIENT_ID}
      - GITHUB_CLIENT_SECRET=${GITHUB_CLIENT_SECRET}
      - GITHUB_APP_ID=${GITHUB_APP_ID}
      - GITHUB_APP_SLUG=${GITHUB_APP_SLUG}
      - SECRET_KEY=${SECRET_KEY}
      - BASE_URL=${BASE_URL}
    restart: unless-stopped

  # Bisect bot (webhook handler)
  bot:
    build:
      context: .
      dockerfile: Dockerfile
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - GITHUB_APP_ID=${GITHUB_APP_ID}
      - GITHUB_PRIVATE_KEY_PATH=/app/secrets/private-key.pem
      - GITHUB_WEBHOOK_SECRET=${GITHUB_WEBHOOK_SECRET}
      - DOCKER_RUNNER_IMAGE=bisect-runner:latest
      - BISECT_TIMEOUT_SECONDS=1800
    volumes:
      - ./secrets:/app/secrets:ro
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      - runner-build
    restart: unless-stopped

  # Build runner image on startup
  runner-build:
    image: docker:24-cli
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./docker:/build:ro
    command: docker build -t bisect-runner:latest -f /build/Dockerfile.runner /build
    restart: "no"

volumes:
  caddy_data:
  caddy_config:
```

> **Note:** PostgreSQL is hosted on Supabase, so no local database container is needed. This simplifies deployment and provides automatic backups.

### 7.3 Caddyfile

Create `Caddyfile`:

```caddyfile
{
    email your-email@example.com
}

your-domain.com {
    # Web app routes
    handle /dashboard* {
        reverse_proxy web:8000
    }
    
    handle /auth* {
        reverse_proxy web:8000
    }
    
    handle /api* {
        reverse_proxy web:8000
    }
    
    # Bot routes
    handle /webhook {
        reverse_proxy bot:8000
    }
    
    handle /health {
        reverse_proxy bot:8000
    }
    
    # Default to web app (landing page)
    handle {
        reverse_proxy web:8000
    }
    
    # Security headers
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
    }
    
    # Enable compression
    encode gzip
    
    # Logging
    log {
        output file /var/log/caddy/access.log
    }
}
```

### 7.4 Environment Variables

Create `.env` file:

```bash
# ======================
# Domain Configuration
# ======================
BASE_URL=https://your-domain.com

# ======================
# Database (Supabase)
# ======================
# Use POOLED connection (port 6543) for the application
# Get this from: Supabase Dashboard → Project Settings → Database → Connection string
DATABASE_URL=postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:6543/postgres

# ======================
# GitHub App (Webhooks & Repo Access)
# ======================
# From GitHub App settings page (see Section 2.2)
GITHUB_APP_ID=123456
GITHUB_APP_SLUG=your-bisect-bot
GITHUB_WEBHOOK_SECRET=your-32-char-webhook-secret

# ======================
# GitHub OAuth App (User Authentication)
# ======================
# From GitHub OAuth App settings (see Section 2.4)
GITHUB_CLIENT_ID=Iv1.abc123def456ghi7
GITHUB_CLIENT_SECRET=your_oauth_client_secret

# ======================
# Session Security
# ======================
# Generate with: openssl rand -hex 32
SESSION_SECRET=your_random_64_char_session_secret

# ======================
# Encryption (Optional but Recommended)
# ======================
# Generate with: openssl rand -hex 32
# Used to encrypt OAuth tokens stored in database
ENCRYPTION_KEY=your_random_64_char_encryption_key
```

> **Important:** Use the **pooled connection string** (port 6543) for the application. The pooler handles connection management efficiently. Use the direct connection (port 5432) only for running migrations.

### 7.5 Secrets Management

Store the GitHub App private key:

```bash
mkdir -p /opt/bisect-bot/secrets
chmod 700 /opt/bisect-bot/secrets

# Copy your private key
cp /path/to/your-private-key.pem /opt/bisect-bot/secrets/private-key.pem
chmod 600 /opt/bisect-bot/secrets/private-key.pem
```

### 7.6 Deploy Commands

#### First-Time Deployment

```bash
# Navigate to project directory
cd /opt/bisect-bot

# Clone the repository
git clone https://github.com/your-org/bisect-bot.git .

# Set up environment variables
cp .env.example .env
nano .env  # Edit with your values

# Set up secrets
mkdir -p secrets
cp /path/to/private-key.pem secrets/private-key.pem
chmod 600 secrets/private-key.pem

# Build and start services
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d

# Run database migrations (first time)
docker compose -f docker-compose.prod.yml exec web alembic upgrade head
```

#### Subsequent Deployments

```bash
cd /opt/bisect-bot

# Pull latest code
git pull origin main

# Rebuild if dependencies changed
docker compose -f docker-compose.prod.yml build

# Run any new migrations
docker compose -f docker-compose.prod.yml exec web alembic upgrade head

# Restart services to pick up code changes
docker compose -f docker-compose.prod.yml up -d
```

#### Useful Commands

```bash
# View logs
docker compose -f docker-compose.prod.yml logs -f

# View logs for specific service
docker compose -f docker-compose.prod.yml logs -f bot

# Restart a specific service
docker compose -f docker-compose.prod.yml restart bot

# Check migration status
docker compose -f docker-compose.prod.yml exec web alembic current

# Rollback last migration (if needed)
docker compose -f docker-compose.prod.yml exec web alembic downgrade -1
```

### 7.7 Database Backups

Supabase handles backups automatically:

| Plan | Backup Frequency | Retention | Point-in-Time Recovery |
|------|------------------|-----------|------------------------|
| **Free** | Daily | 7 days | No |
| **Pro** | Daily | 7 days | Yes (up to 7 days) |
| **Team** | Daily | 14 days | Yes (up to 14 days) |

#### Accessing Backups

1. Go to **Supabase Dashboard** → **Project Settings** → **Database**
2. Scroll to "Backups" section
3. Click on a backup to download or restore

#### Manual Backup (Optional)

If you want additional local backups:

```bash
#!/bin/bash
# backup.sh - Manual backup from Supabase
set -e

BACKUP_DIR="/opt/bisect-bot/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/bisect_${TIMESTAMP}.sql.gz"

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Use direct connection (not pooled) for pg_dump
# Replace with your actual Supabase direct connection string
DIRECT_URL="postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres"

pg_dump "$DIRECT_URL" | gzip > "$BACKUP_FILE"

# Keep only last 7 days of backups
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +7 -delete

echo "Backup created: $BACKUP_FILE"
```

> **Note:** You'll need `postgresql-client` installed on the server: `apt install postgresql-client`

### 7.8 Monitoring

#### Health Check Endpoint

The existing `/health` endpoint can be used for monitoring. Configure your monitoring service to:

1. Check `https://your-domain.com/health` every minute
2. Alert if:
   - Response code is not 200
   - Response time exceeds 5 seconds
   - `docker_available` is false

#### Log Aggregation

View logs with:

```bash
# All services
docker compose -f docker-compose.prod.yml logs -f

# Specific service
docker compose -f docker-compose.prod.yml logs -f bot

# With timestamps
docker compose -f docker-compose.prod.yml logs -f --timestamps
```

#### Optional: Prometheus + Grafana

Add to `docker-compose.prod.yml`:

```yaml
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    restart: unless-stopped

  grafana:
    image: grafana/grafana:latest
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
    volumes:
      - grafana_data:/var/lib/grafana
    restart: unless-stopped
```

---

## 8. Security Checklist

### 8.1 Already Implemented

- [x] **Webhook signature verification** - HMAC-SHA256 validation in `app/main.py`
- [x] **Container isolation** - Bisect jobs run in isolated Docker containers
- [x] **Resource limits** - Memory (2GB) and CPU limits on containers
- [x] **Timeouts** - Configurable timeout (default 30 min) for bisect jobs
- [x] **No shell injection** - Commands passed as array to Docker

### 8.2 To Implement for Production

#### Authentication & Authorization

- [ ] **OAuth state validation** - Prevent CSRF in OAuth flow
- [ ] **Session security** - Use secure, HTTP-only cookies
- [ ] **Token encryption** - Encrypt stored OAuth tokens at rest

```python
# Example: OAuth state validation
import secrets

@router.get("/login")
async def login(request: Request):
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    # ... include state in OAuth URL

@router.get("/callback")
async def callback(request: Request, state: str):
    if not secrets.compare_digest(state, request.session.pop("oauth_state", "")):
        raise HTTPException(400, "Invalid state parameter")
```

#### Rate Limiting

- [ ] **API rate limiting** - Prevent abuse of endpoints
- [ ] **Job rate limiting** - Limit bisect jobs per user/repo

```python
# Example: Using slowapi for rate limiting
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/webhook")
@limiter.limit("10/minute")
async def webhook(request: Request):
    ...
```

#### Infrastructure Security

- [ ] **Non-root containers** - Run as non-root user (already done in Dockerfile)
- [ ] **Read-only file systems** - Where possible, mount as read-only
- [ ] **Network isolation** - Runner containers on isolated network
- [ ] **Secrets rotation** - Rotate webhook secret and API keys periodically

#### Database Security (Supabase)

Supabase provides these security features by default:

- [x] **Encrypted connections** - All connections use SSL/TLS (enforced)
- [x] **Encrypted at rest** - Data encrypted with AES-256
- [x] **Automatic backups** - Daily backups with point-in-time recovery (Pro)
- [x] **Network security** - Database not exposed to public internet (use connection pooler)

Additional steps you should take:

- [ ] **Row Level Security (RLS)** - Enable RLS policies for fine-grained access control
- [ ] **Rotate database password** - Periodically rotate via Supabase dashboard
- [ ] **Monitor connections** - Check Supabase dashboard for unusual activity

```sql
-- Example: Enable RLS on a table
ALTER TABLE bisect_jobs ENABLE ROW LEVEL SECURITY;

-- Example: Policy to restrict access by installation
CREATE POLICY "Users can only see their jobs"
  ON bisect_jobs FOR SELECT
  USING (installation_id IN (
    SELECT installation_id FROM installations
    WHERE installed_by_user_id = current_user_id()
  ));
```

#### Logging & Auditing

- [ ] **Audit logging** - Log security-relevant events
- [ ] **No sensitive data in logs** - Mask tokens, keys, secrets
- [ ] **Log retention** - Define and enforce retention policy

```python
# Example: Audit logging
import logging

audit_logger = logging.getLogger("audit")

def log_audit_event(event_type: str, user_id: int, details: dict):
    audit_logger.info(
        f"event={event_type} user_id={user_id} details={json.dumps(details)}"
    )
```

### 8.3 Security Headers

Already configured in Caddyfile:

```caddyfile
header {
    X-Content-Type-Options nosniff
    X-Frame-Options DENY
    Referrer-Policy strict-origin-when-cross-origin
    Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
}
```

---

## Quick Start Summary

1. **Create GitHub App** at [github.com/settings/apps/new](https://github.com/settings/apps/new)
   - Note the **App ID** from the settings page
   - Generate and download a **private key**
   - Set **webhook secret** (generate with `openssl rand -hex 32`)

2. **Create GitHub OAuth App** at [github.com/settings/developers](https://github.com/settings/developers)
   - Note the **Client ID**
   - Generate and save the **Client Secret**
   - Set callback URL to `https://your-domain.com/auth/callback`

3. **Create Supabase project** at [supabase.com](https://supabase.com) (Frankfurt region)
   - Get the **pooled connection string** (port 6543)

4. **Provision Hetzner VPS** (CX32 recommended)

5. **Configure DNS** to point to your server

6. **Clone repo** and set up environment variables:
   - `GITHUB_APP_ID` - from step 1
   - `GITHUB_WEBHOOK_SECRET` - from step 1
   - `GITHUB_CLIENT_ID` - from step 2
   - `GITHUB_CLIENT_SECRET` - from step 2
   - `DATABASE_URL` - from step 3
   - `SESSION_SECRET` - generate with `openssl rand -hex 32`

7. **Copy private key** to `secrets/private-key.pem`

8. **Run migrations**: `alembic upgrade head` (creates all database tables)

9. **Deploy** with `docker compose -f docker-compose.prod.yml up -d`

10. **Update GitHub App** webhook URL to your production domain

11. **Test** by visiting your domain and signing in with GitHub

For questions or issues, open a GitHub issue on this repository.

