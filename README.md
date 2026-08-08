# RareMachines for Frappe CRM (`raremachines`)

Connects a Frappe CRM site to **[RareMachines](https://whitesense.in)**, so your
team can work their CRM from WhatsApp — ask about leads and deals, and make
changes, in chat.

RareMachines is a hosted service at **https://whitesense.in**. This app is the
piece that runs on your Frappe site and links the two.

This app is **not** a second CRM. It does **not** store leads, deals or
contacts. It only:

1. Proves site ownership and links the site to a RareMachines workspace
2. Registers an **OAuth Client** so each person can authorize as themselves
3. Exposes a small number of authenticated APIs the service needs

**System of record:** always your Frappe CRM.
**RareMachines:** credentials, conversation state and audit — never a durable
copy of your CRM rows.

| | |
|--|--|
| **App name** | `raremachines` |
| **Module** | RareMachines |
| **License** | GPL-3.0 |
| **Frappe** | v16 (bench or Frappe Cloud) |
| **Service** | [whitesense.in](https://whitesense.in) |

---

## How it fits together

```text
┌──────────── RareMachines (whitesense.in) ────────────┐
│  Workspace · chat agent · connections UI             │
│  Site link:  OAuth client id + secret                │
│  Per user:   OAuth bearer token                      │
└─────────────────────────▲────────────────────────────┘
                          │ HTTPS: pair, OAuth, API calls
┌─────────────────────────┴────────────────────────────┐
│  Your Frappe site                                    │
│  apps:      frappe + crm + raremachines              │
│  DocType:   RareMachines Settings                    │
│  OAuth Client: "RareMachines"                        │
│  CRM Leads / Deals / Organizations / Tasks / …       │
└──────────────────────────────────────────────────────┘
```

### Two layers of "connect" — do not skip the second

| Layer | Who | What |
|-------|-----|------|
| **1. Site link** | A System Manager, once | Site ↔ RareMachines workspace, plus the OAuth Client |
| **2. Personal OAuth** | Every person who uses the bot | "Allow RareMachines" as **their own** Frappe user |

Chat runs every CRM action with that person's own bearer token, so Frappe
enforces their roles and User Permissions exactly as it does in Desk. There is
**no** shared admin key.

---

## Requirements

- [Frappe Bench](https://github.com/frappe/bench), or Frappe Cloud with custom apps
- **Frappe** v16
- **Frappe CRM** (`crm`) installed on the same site
- A **RareMachines** account at [whitesense.in](https://whitesense.in)
- A System Manager (or Administrator) on the site, to pair it

---

## Installation

### 1. Get the app onto the bench

The bench app directory **must be named `raremachines`** — it has to match
`app_name` in `hooks.py`. The repository has a different name, so clone it into
the right folder explicitly:

```bash
cd /path/to/frappe-bench
git clone https://github.com/WhitesenseAI/raremachines-frappe-connector.git apps/raremachines
```

Make sure `sites/apps.txt` lists `raremachines`.

Using `bench get-app` instead will create `apps/raremachines-frappe-connector`,
which will not work until renamed:

```bash
bench get-app https://github.com/WhitesenseAI/raremachines-frappe-connector.git --branch version-16
mv apps/raremachines-frappe-connector apps/raremachines
```

### 2. Install on a site

```bash
bench --site your-site install-app raremachines
bench --site your-site migrate
bench --site your-site clear-cache
```

There is nothing to configure. The app talks to **https://whitesense.in** and
to no other host.

### 3. Restart

```bash
bench restart
bench --site your-site clear-cache
```

---

## Open RareMachines Settings

After install, a System Manager can reach it by:

| Method | How |
|--------|-----|
| Awesome Bar | Type **RareMachines Settings** |
| Direct URL | `https://your-site/app/raremachines-settings` |
| Workspace | The **RareMachines** workspace, or the shortcut added to the CRM workspace |

It is **not** under Frappe CRM's gear → Settings → Integrations — that list is
hardcoded in CRM's own UI. See [Known limits](#known-limits).

---

## Pair the site

You need a RareMachines workspace and admin rights on it. Either direction
works; the result is identical.

### Path A — start in RareMachines

1. Sign in to RareMachines and select your workspace.
2. Go to onboarding **Frappe**, or **Connections → Connect Frappe**.
3. Enter your site URL, e.g. `https://crm.example.com`.
4. Click **Connect Frappe site**.
5. Your browser opens the Frappe site — sign in as a System Manager if asked.
6. Confirm. RareMachines Settings then shows **Active**.

### Path B — start in Frappe

1. Open **RareMachines Settings**.
2. Click **Connect with RareMachines**.
3. Sign in to RareMachines if asked, then confirm the workspace.
4. You are returned to Frappe with status **Active**.

### What pairing does on your site

- Creates or updates an **OAuth Client** named **RareMachines**
- Registers the redirect URI `https://whitesense.in/api/integrations/frappe/callback`
- Generates an install id and install secret (HMAC material; the secret is
  never displayed on the form)
- Sends the client credentials to RareMachines over TLS

### After pairing: personal OAuth

Pairing links the site. Each person still has to authorize individually before
they can use chat.

1. In RareMachines **Connections**, click **Connect my Frappe account**.
2. You are taken through Frappe login. Any existing Desk session is cleared
   first, so you cannot silently authorize as Administrator by accident.
3. Sign in as the Frappe user whose email **matches** your RareMachines user.
4. Click **Allow** on the consent screen, which shows who you are signed in as.

**If the emails do not match**, authorization fails — for example a Gmail
RareMachines login against the Frappe `Administrator` account. Sign out of Desk
and use the matching Frappe user.

---

## Configuration reference

| Setting | Where | Notes |
|---------|-------|-------|
| RareMachines service | — | Fixed at `https://whitesense.in`; not configurable |
| Connection status | RareMachines Settings | Disconnected / Active / Error |
| OAuth Client | Desk → OAuth Client → **RareMachines** | Created automatically when you pair |
| Redirect URI | On that OAuth Client | Must match the RareMachines callback exactly |
| Allowed roles on the OAuth Client | Managed by this app | Derived from the roles that can read **CRM Lead** on your site, so custom role names work. `All` is excluded and actively removed — it would let any authenticated account, including Website and portal users, authorize |

---

## APIs this app exposes

These exist for the RareMachines service to call. They are listed for
transparency, not as a public API.

| Method | Purpose |
|--------|---------|
| `raremachines.api.connect.*` | Pair start/finish, disconnect, browser pair, re-login |
| `raremachines.api.permissions.get_capability_permissions` | Reports which DocTypes the signed-in user may read/write/create, via `frappe.has_permission`. Custom roles supported |
| `raremachines.api.org_users.list_crm_users` | Enabled CRM-capable users (email and full name). POST-only and authenticated by an HMAC over `timestamp.body` keyed by this site's install secret; it fails closed if the site is unpaired. This is the one endpoint that sends personal data off-site — see [Data & privacy](#data--privacy) |

The permission probe reports visibility only. Frappe still enforces permissions
on every actual call.

---

## Uninstall

```bash
bench --site your-site uninstall-app raremachines
```

The app cleans up after itself. Before it is removed it will:

- **delete the `RareMachines` OAuth Client**, so no working credential against
  your site survives the uninstall;
- **delete every OAuth bearer token and authorization code** issued to that
  client, so nobody remains authorized;
- **remove the RareMachines shortcut** it added to the CRM workspace.

Nothing is left for you to clean up by hand. Disconnecting inside RareMachines
too is still worth doing, so the workspace stops showing a site it can no
longer reach.

---

## Known limits

| Topic | Detail |
|-------|--------|
| **CRM Integrations menu** | Frappe CRM's Settings → Integrations list is hardcoded in its Vue app, so this app cannot add a row to it without patching CRM. Tracked upstream at [frappe/crm#2172](https://github.com/frappe/crm/issues/2172). Use Desk → **RareMachines Settings** |
| **No CRM data here** | This app stores no Leads or Deals — only pairing, OAuth and settings |
| **Webhooks** | The install secret is in place for signed events; the event pipeline itself lives in the RareMachines service |
| **One site per workspace** | A RareMachines workspace currently models a single Frappe connection |

---

## Data & privacy

What this app sends to RareMachines, and when:

| Data | When | Why |
|---|---|---|
| Your site URL, an install id, and an OAuth client id/secret minted on your site | Once, when a System Manager pairs the site | So RareMachines can run OAuth against your site |
| **Email address and full name of every enabled user who can read CRM Lead** | When a workspace admin opens the "invite teammates" picker | To suggest who to invite. It only suggests — nobody gains access without someone clicking Invite, and each person still completes their own Frappe OAuth |
| The CRM records you ask about | Per request, while you chat | To answer the question you asked |

Worth knowing:

- **Your business data is not copied into RareMachines.** Records are read
  through your site's API at the moment you ask, and are not stored there.
- **Every CRM action runs as the individual user**, on their own OAuth token,
  so your roles and record-level permissions apply exactly as in Desk. This app
  cannot widen anyone's access.
- **This app contacts `https://whitesense.in` and nothing else.**
- Secrets — the install secret and the OAuth client secret — are held in your
  site's own password store. They are never returned by any endpoint and never
  written to a log.

---

## Security notes

- Personal OAuth uses the authorization code flow with PKCE.
- Chat always uses per-user tokens. There is no fallback to a shared System
  Manager key.
- The permission probe is for tool visibility only; Frappe enforces every call.
- Client secrets, pair tickets and install secrets are never logged.

---

## Development

For working on this connector.

```bash
cd apps/raremachines
pre-commit install        # optional

# after changing code on a path-installed app
bench --site your-site migrate
bench --site your-site clear-cache
bench restart             # so Python hooks reload
```

Tests:

```bash
bench --site your-site set-config allow_tests true
bench --site your-site run-tests --app raremachines
```

| Path | Role |
|------|------|
| `raremachines/api/connect.py` | Pairing, OAuth client lifecycle, re-login |
| `raremachines/api/permissions.py` | Capability permission probe |
| `raremachines/api/org_users.py` | HMAC-signed staff roster endpoint |
| `raremachines/config/saas.py` | Resolves the RareMachines service URL |
| `raremachines/raremachines/doctype/raremachines_settings/` | Settings Single |
| `raremachines/install.py` | Install hooks: settings row, CRM shortcut |
| `raremachines/uninstall.py` | Removes the OAuth client, its tokens and the shortcut |
| `raremachines/hooks.py` | App metadata and hooks |
| `raremachines/tests/` | Security regression suite |

---

## License

GPL-3.0 — see `license.txt`.

---

## Support

- **Documentation:** [Install and use guide](https://github.com/WhitesenseAI/raremachines-frappe-connector/tree/version-16/docs)
- **Support:** [whitesense.in/support](https://whitesense.in/support) · contact@whitesense.in
- **Privacy:** [whitesense.in/privacy](https://whitesense.in/privacy)
- **Terms:** [whitesense.in/terms](https://whitesense.in/terms)
- **Source:** [WhitesenseAI/raremachines-frappe-connector](https://github.com/WhitesenseAI/raremachines-frappe-connector)
- **Publisher:** Whitefield Labs
