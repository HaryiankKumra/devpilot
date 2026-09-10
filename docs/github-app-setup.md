# Connecting DevPilot to a real GitHub account

DevPilot ships in **mock mode**, which needs no credentials at all. Everything
runs: registration, login, repository sync, webhook handling, the dashboard. The
GitHub calls are served by an in-process fake that returns the same validated
types the real client returns.

This document is for when you want it talking to the real GitHub.

- [What you actually need](#what-you-actually-need)
- [Step 1 — expose your machine to the internet](#step-1--expose-your-machine-to-the-internet)
- [Step 2 — register the GitHub App](#step-2--register-the-github-app)
- [Step 3 — collect the credentials](#step-3--collect-the-credentials)
- [Step 4 — configure DevPilot](#step-4--configure-devpilot)
- [Step 5 — install and verify](#step-5--install-and-verify)
- [Permissions, and why each one](#permissions-and-why-each-one)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)

---

## What you actually need

Six values. Five come from one GitHub App registration; the sixth you invent.

| Setting | Where it comes from |
| --- | --- |
| `DEVPILOT_GITHUB_APP_ID` | The App's settings page |
| `DEVPILOT_GITHUB_APP_SLUG` | The App's public URL |
| `DEVPILOT_GITHUB_APP_PRIVATE_KEY_PATH` | A `.pem` you generate and download |
| `DEVPILOT_GITHUB_WEBHOOK_SECRET` | **You choose it** when creating the App |
| `DEVPILOT_GITHUB_CLIENT_ID` | The App's settings page |
| `DEVPILOT_GITHUB_CLIENT_SECRET` | Generated on the App's settings page |

Nothing costs money. A GitHub App on a personal account is free, and you can
install it on your own repositories.

## Step 1 — expose your machine to the internet

GitHub delivers webhooks by making an HTTP request **to you**, so
`localhost:8000` is unreachable to it. You need a public URL that forwards to
your local API.

Any tunnel works. Two common ones:

```bash
# ngrok
ngrok http 8000

# cloudflared
cloudflared tunnel --url http://localhost:8000
```

Either prints a public HTTPS URL, e.g. `https://abc123.ngrok-free.app`. Keep the
tunnel running — the URL usually changes each time you restart it, and you will
have to update the App's webhook URL when it does.

> Skip this step if you only want OAuth linking and repository sync. Those are
> outbound calls and work without a tunnel. Only *incoming* webhooks need one.

## Step 2 — register the GitHub App

Go to **Settings → Developer settings → GitHub Apps → New GitHub App**
([github.com/settings/apps/new](https://github.com/settings/apps/new)).

Fill in:

| Field | Value |
| --- | --- |
| **GitHub App name** | Anything unique, e.g. `devpilot-yourname` |
| **Homepage URL** | `http://localhost:5173` |
| **Callback URL** | `http://localhost:8000/api/v1/github/callback` |
| **Request user authorization (OAuth) during installation** | ✅ ticked |
| **Webhook → Active** | ✅ ticked |
| **Webhook URL** | `https://YOUR-TUNNEL-URL/api/v1/webhooks/github` |
| **Webhook secret** | Generate one and save it (see below) |

Generate the webhook secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The callback URL must match `DEVPILOT_GITHUB_OAUTH_REDIRECT_URI` character for
character, including the scheme and any trailing slash. A mismatch produces a
`redirect_uri_mismatch` error at the end of the OAuth flow.

Set the permissions and events described in
[Permissions, and why each one](#permissions-and-why-each-one) below, then
create the App.

## Step 3 — collect the credentials

On the App's settings page after creation:

1. **App ID** — shown near the top. A number like `123456`.
2. **Client ID** — shown just below it, looks like `Iv23liABCDEFGH`.
3. **Client secret** — press *Generate a new client secret*. **Copy it now**;
   GitHub never shows it again.
4. **Private key** — scroll to the bottom, press *Generate a private key*. A
   `.pem` file downloads. This is the most sensitive value here — see
   [Security notes](#security-notes).
5. **App slug** — the last path segment of the App's public URL. For
   `https://github.com/apps/devpilot-yourname` the slug is `devpilot-yourname`.

## Step 4 — configure DevPilot

Move the private key somewhere outside the repository, then fill in `.env`:

```bash
mkdir -p ~/.devpilot
mv ~/Downloads/devpilot-yourname.*.private-key.pem ~/.devpilot/github-app.pem
```

```dotenv
DEVPILOT_GITHUB_MODE=live

DEVPILOT_GITHUB_APP_ID=123456
DEVPILOT_GITHUB_APP_SLUG=devpilot-yourname
DEVPILOT_GITHUB_APP_PRIVATE_KEY_PATH=/home/you/.devpilot/github-app.pem

DEVPILOT_GITHUB_WEBHOOK_SECRET=the-secret-you-generated
DEVPILOT_GITHUB_CLIENT_ID=Iv23liABCDEFGH
DEVPILOT_GITHUB_CLIENT_SECRET=the-client-secret

DEVPILOT_GITHUB_OAUTH_REDIRECT_URI=http://localhost:8000/api/v1/github/callback
DEVPILOT_FRONTEND_BASE_URL=http://localhost:5173
```

Under Docker Compose, mount the key into the container and use the in-container
path:

```yaml
services:
  api:
    volumes:
      - ~/.devpilot/github-app.pem:/run/secrets/github-app.pem:ro
    environment:
      DEVPILOT_GITHUB_APP_PRIVATE_KEY_PATH: /run/secrets/github-app.pem
```

Restart the API so the new settings are read.

## Step 5 — install and verify

1. Visit `https://github.com/apps/YOUR-APP-SLUG` and press **Install**. Choose
   the repositories to grant access to.
2. In DevPilot, sign in and go to **Settings → Connect GitHub**. This runs the
   OAuth flow and records your GitHub id, which is how DevPilot knows which
   local account owns the installation.
3. Check the API can authenticate as the App:

   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/v1/repositories/installations
   ```

   A list containing your installation means the App ID, private key and
   permissions are all correct.
4. Sync the repositories:

   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"installation_id": 12345678}' \
     http://localhost:8000/api/v1/repositories/sync
   ```

5. Open a pull request on one of the installed repositories. GitHub's
   **Advanced** tab on the App settings page shows every delivery, its request
   body and DevPilot's response — the fastest way to see what went wrong.

> **Note for the current milestone.** A pull request creates a `review_jobs` row
> with status `queued`, and nothing processes it yet: the Celery worker arrives
> in Milestone 5. Seeing a queued job is the expected, correct outcome today.

## Permissions, and why each one

Request the minimum. Every extra permission widens what a leaked installation
token can reach.

**Repository permissions**

| Permission | Access | Why |
| --- | --- | --- |
| Contents | Read-only | Fetch file contents for indexing and retrieval |
| Metadata | Read-only | Mandatory; GitHub adds it automatically |
| Pull requests | **Read and write** | Read the diff, and post review comments |

**Subscribe to events**

| Event | Why |
| --- | --- |
| Pull request | The trigger for a review |
| Installation | Learn which repositories DevPilot may act on |
| Installation repositories | Track access being granted or revoked |

Write access on pull requests is the only write permission, and it exists solely
so DevPilot can post its findings back. It cannot push commits, change settings,
or merge anything.

## Troubleshooting

**Every webhook returns 401.** The secret in `.env` does not match the one on the
App. Note that DevPilot rejects all deliveries when no secret is configured —
that is deliberate, since an unverified webhook is unauthenticated input that
can create work.

**`redirect_uri_mismatch` after authorising.** The callback URL on the App and
`DEVPILOT_GITHUB_OAUTH_REDIRECT_URI` differ. They must be identical, including
scheme and trailing slash.

**503 with `github_not_configured`.** `DEVPILOT_GITHUB_MODE=live` but a required
value is missing. The error message names the variable.

**502 with `github_access_denied`.** The App is installed but lacks a
permission, or was never granted access to that repository. Adding a permission
after installation requires the installing user to approve it — GitHub emails
them a request.

**"private key could not be used to sign a token".** The `.pem` is truncated or
the wrong file. It must begin `-----BEGIN RSA PRIVATE KEY-----` and end with the
matching footer.

**Webhooks never arrive.** Check the tunnel is still running and that the App's
webhook URL matches its current public URL. Tunnel URLs usually change on
restart.

## Security notes

**The private key is the App's identity.** Anyone holding it can act as DevPilot
on every repository where it is installed. It must never be committed —
`.gitignore` already excludes `*.pem`. If it leaks, generate a new key on the
App's settings page and delete the old one; existing installations keep working.

**Installation tokens are deliberately short-lived.** DevPilot exchanges the
private key for a token valid for one hour, scoped to a single installation.
That is why a leaked token is survivable and a leaked private key is not.

**The OAuth token only proves identity.** DevPilot requests `read:user` and
nothing more. Repository access comes from the App installation, so the OAuth
token cannot read code even if it leaks.

**Rotate the client secret freely.** It is used only during the OAuth exchange;
regenerating it invalidates nothing except in-flight sign-ins.
