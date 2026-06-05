# CI/CD Pipeline

This document describes the CI/CD pipeline for the Odoo MCP Server.

## Overview

```
PR Created → CI Checks → Automerge → Deploy to Staging
                                          ↓
                                   (manual) Deploy to Production
```

## Workflows

### 1. CI Workflow (`.github/workflows/ci.yml`)

Triggered on:
- Push to `main`
- Pull requests to `main`

**Jobs:**

| Job | Description | Dependencies |
|-----|-------------|--------------|
| `lint` | Runs `ruff check` and `mypy` type checking | None |
| `unit-tests` | Runs `pytest tests/unit` with coverage (no external services) | None |
| `integration-tests` | Runs `pytest tests/integration` against a real Odoo | Only on `main` or with `run-integration` label |
| `e2e-tests` | Runs the full MCP HTTP stack tests | `lint`, `unit-tests` (main / `run-integration`) |
| `security` | Bandit + pip-audit + safety dependency scan | None |
| `build` | Builds Docker image | `lint`, `unit-tests`, `security` |
| `deploy-staging` | Deploys to Cloud Run staging | `build`, `integration-tests`, `e2e-tests` (only on `main`) |

> The `integration-tests`, `e2e-tests`, and `deploy-staging` jobs are only useful
> once you have configured the secrets below. Without them they skip or no-op,
> so a fresh fork's `lint` / `unit-tests` / `security` / `build` jobs pass out of
> the box.

### Optional: AI / Security PR Reviewers

Two optional workflows run on pull requests when configured:

- `ai-review.yml` — general AI code review via Gemini (`GEMINI_API_KEY`).
- `security-review.yml` — security review using Claude via Vertex AI (Workload
  Identity Federation, no static key).

Both **skip (stay green)** when their secrets are unset: `ai-review` exits early
without a `GEMINI_API_KEY`, and `security-review` skips unless
`GCP_WORKLOAD_IDENTITY_PROVIDER` is configured. So a fork that hasn't provisioned
the reviewer kit still gets green PRs. The `terraform/` directory provisions the
required GCP resources and GitHub secrets — see
[`terraform/README.md`](../terraform/README.md).

### 2. Automerge Workflow (`.github/workflows/automerge.yml`)

Triggered when:
- CI workflow completes successfully on a PR branch

**Behavior:**
- Automatically merges PRs when all CI checks pass
- Uses **squash merge** to keep history clean
- Commit message uses the PR title
- Retries up to 6 times if merge fails (e.g., due to branch protection)

## Development Workflow

### Creating a PR

```bash
# Create feature branch
git checkout -b feature/my-feature

# Make changes and commit
git add .
git commit -m "feat: Add my feature"

# Push and create PR
git push -u origin feature/my-feature
gh pr create --title "feat: Add my feature" --body "Description"
```

### What Happens After PR Creation

1. **CI runs** - lint, tests, build
2. **Automerge triggers** - when CI passes, PR is automatically merged
3. **Deploy to staging** - merge to `main` triggers staging deployment

### Skipping Automerge

To prevent automerge, add the `no-automerge` label to your PR.

## Environments

### Staging

- **Service:** `odoo-mcp-server` (Cloud Run)
- **Region:** `your-gcp-region`
- **URL:** `https://your-mcp-server.run.app`
- **Deployed:** Automatically on merge to `main`

### Production

- **Status:** Not yet configured
- **Deployment:** Manual approval required in GitHub Actions

## Configuration

### Repository Variables (Settings → Variables)

| Variable | Description | Default |
|----------|-------------|---------|
| `GCP_REGION` | Cloud Run / Vertex region | `europe-west1` |
| `REQUIRE_STAGING` | Set to `true` if this repo runs a staging environment. Makes a missing `STAGING_ODOO_URL` fail CI. | unset (single-instance) |

### Single instance vs. two environments (staging)

A `config` job resolves whether a staging Odoo is configured (`STAGING_ODOO_URL`
secret present) and the `integration-tests`, `e2e-tests`, and `deploy-staging` jobs
gate on it. This makes CI behave correctly for both deployment shapes:

- **Single instance (no staging) — CI stays green.** Leave `STAGING_ODOO_URL` and
  `REQUIRE_STAGING` unset. The staging jobs **skip** (they need a staging Odoo to run
  against); `lint`, `unit-tests`, `security`, and `build` still run. This is the
  expected, healthy state for a one-Odoo deployment.
- **Two environments — CI runs the staging jobs.** Set the `STAGING_ODOO_URL` secret
  (plus the related Odoo/OAuth secrets below) and the integration/e2e/deploy jobs run
  on `main`.
- **Two environments, misconfigured — CI goes red (by design).** If you set the
  `REQUIRE_STAGING=true` variable to declare that this repo *should* have staging but
  the `STAGING_ODOO_URL` secret is missing, the `config` job **fails loudly**. This
  red is the intended signal of a misconfiguration, not a broken pipeline.

### GitHub Secrets

Deployment + integration/e2e jobs:

| Secret | Used by | Description |
|--------|---------|-------------|
| `GCP_PROJECT_ID` | deploy | GCP project ID for the GCR image + Cloud Run |
| `GCP_SA_KEY` | deploy | Service account JSON key for GCP auth |
| `STAGING_ODOO_URL` | deploy, integration, e2e | Odoo instance URL |
| `STAGING_ODOO_DB` | deploy, integration, e2e | Odoo database name |
| `STAGING_ODOO_USERNAME` | deploy | Odoo service-account username |
| `ODOO_API_KEY` / `TEST_ODOO_API_KEY` | integration, e2e | Odoo API key |
| `ODOO_USERNAME` | integration, e2e | Odoo username |
| `OAUTH_CLIENT_ID` | deploy | Google OAuth client ID |
| `OAUTH_RESOURCE_IDENTIFIER` | deploy | Public URL of the deployed server |
| `OAUTH_REDIRECT_URI` | deploy | OAuth callback URL |
| `INTERNAL_EMAIL_DOMAIN` | deploy | Domain granted extended write scopes |
| `MCP_API_KEY_EMAIL` | deploy | Identity email for the API-key client |
| `TEST_USER_EMAIL` | e2e | Email mapped to a test employee |
| `TEST_MCP_SERVER_URL`, `TEST_AUTH_SERVER_URL` | integration | Test endpoints |

Optional AI/security reviewers (provisioned by `terraform/`):

| Secret | Used by | Description |
|--------|---------|-------------|
| `GEMINI_API_KEY` | ai-review | Gemini API key |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | security-review | WIF provider resource name |
| `GCP_SERVICE_ACCOUNT` | security-review | Reviewer service-account email |
| `VERTEX_PROJECT_ID` | security-review | GCP project for Vertex AI |

### GCP Secrets (Secret Manager)

Mounted into Cloud Run by the deploy job:

| Secret | Description |
|--------|-------------|
| `odoo-api-key` | Odoo API key for the service account |
| `oauth-client-secret` | Google OAuth client secret |
| `mcp-api-key` | API key for CLI clients (Claude Code) |

## Troubleshooting

### CI Failing

1. Check the failed job in GitHub Actions
2. Common issues:
   - `lint` - Run `ruff check .` locally to fix
   - `mypy` - Run `mypy src/ --ignore-missing-imports` locally
   - `unit-tests` - Run `pytest tests/unit -v` locally

### Automerge Not Working

1. Ensure CI has passed (green checkmark)
2. Check if `no-automerge` label is present
3. Verify GitHub token has write permissions
4. Check automerge workflow logs for errors

### Deployment Not Triggering

1. Automerge must complete first
2. Check if merge happened to `main` branch
3. Review `deploy-staging` job logs in GitHub Actions

## Manual Operations

### Force Deploy (Emergency Only)

```bash
# Only use if CI/CD is broken
gcloud run deploy odoo-mcp-server \
  --image gcr.io/your-gcp-project/odoo-mcp-server:latest \
  --region your-gcp-region
```

### Check Deployment Status

```bash
# Health check
curl https://your-mcp-server.run.app/health

# View logs
gcloud run logs read odoo-mcp-server --region=your-gcp-region --limit=50
```
