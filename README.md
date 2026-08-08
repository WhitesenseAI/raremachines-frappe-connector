# RareMachines for Frappe CRM (`raremachines`)

Thin **Frappe app** that pairs a customer’s Frappe / Frappe CRM site with **[RareMachines](https://github.com/Whitefield-Labs/RareMachines)** (the AI agent SaaS).

This app is **not** a second CRM and does **not** store leads, deals, or contacts.  
It only:

1. Proves site ownership and links the site to a RareMachines workspace  
2. Registers an **OAuth Client** so each RareMachines user can authorize as themselves  
3. Exposes a few secure APIs RareMachines needs (pair finish, re-login, permission probe)

**System of record:** always Frappe CRM.  
**RareMachines:** credentials, conversation state, audit — never a durable copy of your CRM rows.

| | |
|--|--|
| **App name** | `raremachines` |
| **Module** | RareMachines |
| **License** | GPL-3.0 |
| **Frappe** | v16-oriented (bench / Frappe Cloud style install) |
| **Companion SaaS** | [RareMachines](https://whitesense.in) |

---

## How it fits together

```text
┌──────────────── RareMachines SaaS ────────────────┐
│  Workspace + Connections UI + Chat agent     │
│  Site link: OAuth Client id/secret           │
│  Per user: UserConnectionGrant (Bearer)      │
└──────────────────────▲───────────────────────┘
                       │ HTTPS pair + OAuth + API
┌──────────────────────┴───────────────────────┐
│  Customer Frappe site                        │
│  apps: frappe + crm + raremachines           │
│  DocType: RareMachines Settings                   │
│  OAuth Client: "RareMachines"                     │
│  CRM Leads / Deals / Orgs / Tasks / …        │
└──────────────────────────────────────────────┘
```

### Two layers of “connect” (do not skip #2)

| Layer | Who | What |
|-------|-----|------|
| **1. Site link** | System Manager / admin once | Site ↔ RareMachines workspace + OAuth Client |
| **2. Personal OAuth** | Every person who uses the bot | “Allow RareMachines” as **their** Frappe user |

Chat CRM tools run with that user’s Bearer token. Frappe enforces **roles + User Permissions**.  
There is **no** shared admin API key for multi-user chat.

---

## Requirements

- [Frappe Bench](https://github.com/frappe/bench) (or Frappe Cloud with custom apps)
- **Frappe** framework (v16 recommended; match your site’s major version)
- **Frappe CRM** app (`crm`) installed on the same site
- A running **RareMachines** deployment the site can reach (local or cloud)
- Site System Manager (or Administrator) for pair

---

## Installation

### 1. Get the app onto the bench

The bench **app directory must be named `raremachines`** (matches `app_name` in `hooks.py`).  
This GitHub repo is named `frappe-plugin-raremachines`, so clone **into** that folder name:

```bash
cd /path/to/frappe-bench

# Recommended: clone directly as apps/raremachines
git clone https://github.com/Whitefield-Labs/frappe-plugin-raremachines.git apps/raremachines

# Then tell bench about the app (if not already listed)
# apps.txt should include: raremachines
# Or: bench setup requirements  (as needed for your bench version)

# Alternative with bench get-app (may create apps/frappe-plugin-raremachines):
# bench get-app https://github.com/Whitefield-Labs/frappe-plugin-raremachines.git --branch main
# mv apps/frappe-plugin-raremachines apps/raremachines
```

Local path install while developing:

```bash
# If the app already lives under apps/raremachines,
# just migrate / install-app — no re-clone needed.
```

### 2. Install on a site

```bash
bench --site your-site.local install-app raremachines
bench --site your-site.local migrate
bench --site your-site.local clear-cache
```

### 3. Point the site at RareMachines (base URL)

RareMachines’s cloud URL is **not** a free-text field for end users. It is resolved as:

1. `site_config.json` → `"raremachines_base_url"` (or the pre-rename
   `"conduit_base_url"`, still honoured so sites configured before the rename
   keep working)  
2. Else if `developer_mode` → `http://localhost:3000`  
3. Else default cloud URL **`https://whitesense.in`** (`raremachines/config/saas.py`)

**Local RareMachines + local Frappe:**

```bash
bench --site your-site.local set-config raremachines_base_url http://localhost:3000
```

**Production (default without site_config):** `https://whitesense.in`

Override only if you need another host:

```bash
bench --site your-site.local set-config raremachines_base_url https://whitesense.in
```

Or in `sites/your-site.local/site_config.json`:

```json
{
  "raremachines_base_url": "https://whitesense.in"
}
```

Use **https** for non-local hosts. `http://localhost` / `http://127.0.0.1` are allowed for development.

### 4. Restart / reload

```bash
bench restart
# or, in dev: restart `bench start` / `bench serve`
bench --site your-site.local clear-cache
```

---

## Open RareMachines Settings

After install, System Managers can open:

| Method | How |
|--------|-----|
| Awesome Bar | Type **RareMachines Settings** |
| Direct URL | `https://your-site/app/raremachines-settings` |
| Workspace | **RareMachines** workspace / CRM shortcut (when install hooks ran) |

**Not** under Frappe CRM gear → Settings → Integrations (CRM SPA list is hardcoded; see “Known limits”).

---

## Pair the site with RareMachines (site link)

You need a RareMachines workspace (admin). Two equivalent paths.

### Path A — Start in RareMachines (website-first)

1. Sign in to RareMachines → create/select workspace.  
2. Onboarding **Frappe** or **Connections → Connect Frappe**.  
3. Enter site URL, e.g. `https://crm.customer.com` or local `http://conduit.localhost:8000`.  
4. Click **Connect Frappe site**.  
5. Browser opens this Frappe site (login as System Manager if needed).  
6. Pair completes → RareMachines Settings shows **Active**.

### Path B — Start on Frappe Desk (Frappe-first)

1. Open **RareMachines Settings**.  
2. Confirm RareMachines cloud URL (read-only) is correct.  
3. Click **Connect with RareMachines**.  
4. Sign in to RareMachines if needed → **confirm** workspace.  
5. Return to Frappe → status **Active**.

### What pair does on the site

- Creates/updates **OAuth Client** app name **RareMachines**  
- Registers redirect URI:  
  `{raremachines_base_url}/api/integrations/frappe/callback`  
- Generates install id + install secret (HMAC material for future webhooks; secret not shown on form)  
- POSTs client credentials to RareMachines `pair/complete` (over TLS / local HTTP)

### After pair: personal OAuth (required for chat)

1. In RareMachines **Connections**, click **Connect my Frappe account**.  
2. You will be forced through Frappe **login** (Desk session is cleared first so you don’t silently authorize as Administrator).  
3. Sign in as the **same email** as your RareMachines user.  
4. **Allow** RareMachines on the consent screen (shows signed-in user).  
5. Chat CRM tools then run **as that user**.

**Email mismatch:** RareMachines Gmail + Frappe Administrator (`admin@example.com`) will fail. Log out of Desk, use the matching Frappe user.

---

## Configuration reference

| Setting | Where | Notes |
|---------|--------|--------|
| RareMachines base URL | `site_config.raremachines_base_url` or defaults | Locked on the form |
| Connection status | RareMachines Settings | Disconnected / Active / Error |
| OAuth Client | Desk → OAuth Client → **RareMachines** | Auto-created on pair |
| Redirect URI | OAuth Client | Must match RareMachines callback exactly |
| Allowed roles on OAuth Client | Includes **All** for personal connect | Adjust only if you understand impact |


---

## APIs this app exposes (for RareMachines)

| Method | Purpose |
|--------|---------|
| `raremachines.api.connect.*` | Pair start/finish, disconnect, browser pair, `oauth_relogin` |
| `raremachines.api.permissions.get_capability_permissions` | Roles + DocType read/write/create via `frappe.has_permission` (custom roles supported) |
| `raremachines.api.org_users.list_crm_users` | Enabled CRM-capable users (email + full name). POST-only, `allow_guest`, but authenticated by an HMAC over `timestamp.body` keyed by this site's `install_secret` — it fails closed if the site is unpaired. This is the one endpoint that sends personal data off-site; see **Data & privacy**. |

Do not call these from untrusted clients without understanding auth; pair/finish is for System Manager / controlled browser flows; permission probe runs as the OAuth user.

---

## Uninstall

```bash
bench --site your-site.local uninstall-app raremachines
```

Uninstall cleans up after itself. Before the app is removed it will:

- **delete the `RareMachines` OAuth Client**, so no working credential against your
  site survives the uninstall;
- **delete every OAuth Bearer Token and Authorization Code** issued to that
  client, so no user remains authorized to RareMachines;
- **remove the RareMachines shortcut** it added to the Frappe CRM workspace.

Nothing is left for you to clean by hand. Disconnecting inside RareMachines as well
is still worth doing so the workspace stops showing a site it can no longer
reach.

---

## Development

```bash
cd apps/raremachines
# optional
pre-commit install

# After code changes on a path-installed app:
bench --site your-site.local migrate
bench --site your-site.local clear-cache
# restart web worker so Python hooks reload
```

Useful files:

| Path | Role |
|------|------|
| `raremachines/api/connect.py` | Pair, OAuth Client mint, re-login |
| `raremachines/api/permissions.py` | Capability permission probe |
| `raremachines/config/saas.py` | Base URL resolution |
| `raremachines/raremachines/doctype/raremachines_settings/` | Settings Single |
| `raremachines/api/org_users.py` | HMAC-signed staff roster endpoint (see Data & privacy) |
| `raremachines/uninstall.py` | Removes the OAuth client, its tokens and the CRM shortcut on uninstall |
| `raremachines/hooks.py` | Install/uninstall hooks |

---

## Known limits

| Topic | Detail |
|-------|--------|
| **CRM SPA Integrations menu** | Frappe CRM Settings → Integrations is a hardcoded Vue list. This app cannot inject a row without patching CRM or waiting for UI extension hooks. Track: [frappe/crm#2172](https://github.com/frappe/crm/issues/2172). Use Desk **RareMachines Settings**. |
| **No CRM data in this app** | No Lead/Deal tables here — only pair/OAuth/settings. |
| **Webhooks** | Install secret is prepared for signed events; full event pipeline is owned by RareMachines SaaS (id-only ack endpoint today). |
| **Multi-site per workspace** | RareMachines product currently models one Frappe connection per workspace. |

---

## Data & privacy

What this app sends to RareMachines, and when:

| Data | When | Why |
|---|---|---|
| Site URL, an install id, and an OAuth client id/secret minted on your site | Once, when a System Manager pairs the site | So RareMachines can run OAuth against your site |
| **Email address and full name of every enabled System User who can read CRM Lead** | When a workspace admin opens the "invite teammates" picker in RareMachines | To suggest who to invite. It suggests only — nobody is granted access without a human clicking Invite, and each person still completes their own Frappe OAuth |
| CRM records you ask about | Per request, while you chat | To answer the question you asked |

Notes worth knowing:

- **Your business data is not copied into RareMachines.** Records are read through
  your site's API at the moment you ask and are not stored there.
- **Every CRM action runs as the individual user**, on their own OAuth token, so
  your Frappe role and record-level permissions apply exactly as they do in
  Desk. This app cannot widen anyone's access.
- **This app talks to `https://whitesense.in` by default.** If you self-host
  RareMachines, set `raremachines_base_url` in your `site_config.json` before pairing.
- Secrets (`install_secret`, OAuth client secret) are stored in your site's own
  password store and are never returned by any endpoint or written to a log.

---

## Security notes

- Prefer **https** RareMachines base URLs in production.  
- Personal OAuth uses Authorization Code (+ PKCE on RareMachines side).  
- Never log client secrets, pair tickets, or install secrets.  
- Chat must use **per-user** tokens; do not fall back to a shared System Manager key for multi-user agents.  
- Permission probe is for **tool visibility** only; Frappe still enforces every API call.

---

## License

GPL-3.0 — see `license.txt`.

---

## Support / product

- **Support:** [whitesense.in/support](https://whitesense.in/support) · contact@whitesense.in  
- **Privacy:** [whitesense.in/privacy](https://whitesense.in/privacy)  
- This plugin: [Whitefield-Labs/frappe-plugin-raremachines](https://github.com/Whitefield-Labs/frappe-plugin-raremachines)  
- Publisher: Whitefield Labs  
