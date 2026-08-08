# RareMachine for Frappe CRM — installation and use

This guide is for the person installing the app on their own Frappe site. If
you are working on the app itself, see the [Development](../README.md#development)
section of the main README instead.

This app connects your Frappe CRM site to
[RareMachine](https://whitesense.in), so your team can work the CRM from
WhatsApp — ask about leads and deals, and make changes, in chat. Your Frappe
site stays the system of record. RareMachine reads from it when you ask, and
writes to it when you approve.

---

## Before you start

- A Frappe site running **Frappe v16**, with the **Frappe CRM** app installed.
- The **System Manager** role on that site — linking the site is an
  administrator action.
- A RareMachine account. Sign up at [whitesense.in](https://whitesense.in).
- The site must be reachable from the internet over **https**. RareMachine
  calls it back during linking to confirm you own it.

---

## 1. Install the app

### Frappe Cloud

Add `raremachines-frappe-connector` to your bench as a custom app, then install
it on the site from the Apps tab.

### Your own bench

The bench app directory **must be named `raremachines`** — it has to match
`app_name` in `hooks.py` — so clone it into that name explicitly:

```bash
cd /path/to/frappe-bench
git clone https://github.com/WhitesenseAI/raremachines-frappe-connector.git apps/raremachines

bench --site your-site install-app raremachines
bench --site your-site migrate
bench restart
```

Make sure `sites/apps.txt` lists `raremachines`.

There is nothing to configure. The app talks to RareMachine at
`https://whitesense.in` and to no other host.

---

## 2. Link the site

Linking connects the site itself. It happens once, and only an administrator
can do it. You can start from either end; the result is the same.

**From RareMachine** — go to Connections, choose *Connect Frappe*, enter your
site URL, and approve on Frappe when your browser lands there.

**From Frappe** — open **RareMachines Settings** (type it in the awesome bar, or
use the RareMachine shortcut on your CRM workspace) and click *Connect with
RareMachine*.

When it works, RareMachines Settings shows **Active**. Behind the scenes the app
creates an OAuth Client named *RareMachine* on your site — that is what lets
each person sign in as themselves in the next step.

> RareMachines Settings is **not** in Frappe CRM's own Settings → Integrations
> list. That list is hardcoded in the CRM app's UI and cannot be added to
> ([frappe/crm#2172](https://github.com/frappe/crm/issues/2172)). Use the Desk
> page.

---

## 3. Connect your own account

Linking the site is not enough to use chat. **Every person connects
individually**, so that every action runs as them.

1. In RareMachine, open **Connections** and click *Connect my Frappe account*.
2. You are taken through Frappe login. Any existing Desk session is cleared
   first, so you cannot authorise as Administrator by accident.
3. Sign in as the Frappe user whose **email matches** your RareMachine user,
   then click **Allow**.

If the emails differ the authorisation fails — that is the check working. Sign
out of Desk and use the matching Frappe user.

---

## Whose permissions apply

Yours. Every read and write runs on your own OAuth token, so Frappe enforces
your roles and record-level permissions exactly as it does in Desk. This app
cannot widen anyone's access, and a teammate who cannot see a record in Frappe
cannot see it through chat either.

Changes pause for approval before they run. You see what is about to happen and
confirm it.

---

## What is sent to RareMachine

One thing is worth calling out explicitly: when a workspace admin opens the
invite-teammates picker, your site returns the **email address and full name of
each enabled user who can read CRM records**, so the admin can choose who to
invite. That request is cryptographically signed and time-limited, and it only
suggests — nobody gains access until someone clicks Invite and completes their
own Frappe login.

Your business records are never copied to RareMachine. Full detail:
[whitesense.in/privacy](https://whitesense.in/privacy).

---

## If something goes wrong

Errors appear on the RareMachines Settings page. What each one means:

| Message | What to do |
|---|---|
| That RareMachine workspace is already linked to a different Frappe site | The workspace points at another site. In RareMachine, disconnect that site first — or tick **Replace** when you link, which drops the old link and every teammate's personal login for it. |
| This Frappe site is already linked to a different RareMachine workspace | Someone else linked this site. Ask that workspace's admin to disconnect, or join their workspace instead of linking the site again. |
| Connect link expired | The link is single-use and short-lived. Click *Connect with RareMachine* again. |
| Could not reach RareMachine | Your Frappe site could not make an outbound connection. Check egress/firewall rules allow it to reach `whitesense.in`. |
| RareMachine could not reach this site at the URL it was given | RareMachine calls your site back during linking to prove it is reachable at the URL you entered. Check the site is reachable from the internet at that exact URL, including https. |
| RareMachine rejected this site's URL | The URL must be a public https address. Private, loopback and internal addresses are refused on purpose. |
| You need the System Manager role on this site | Linking is an administrator action. Sign in to Frappe as a System Manager and retry. |
| RareMachine is not configured correctly on this site | Contact support — this one is on our side, not yours. |

---

## Uninstalling

```bash
bench --site your-site uninstall-app raremachines
```

The app cleans up after itself. Before it is removed it will:

- **delete the `RareMachine` OAuth Client**, so no working credential against
  your site survives the uninstall;
- **delete every OAuth bearer token and authorisation code** issued to that
  client, so nobody remains authorised;
- **remove the RareMachine shortcut** it added to your CRM workspace.

Nothing is left for you to clean by hand. Disconnecting inside RareMachine as
well is still worth doing, so the workspace stops showing a site it can no
longer reach.

---

## Help

- **Support:** [whitesense.in/support](https://whitesense.in/support) · contact@whitesense.in
- **Privacy:** [whitesense.in/privacy](https://whitesense.in/privacy)
- **Terms:** [whitesense.in/terms](https://whitesense.in/terms)
