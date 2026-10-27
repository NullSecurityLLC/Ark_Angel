# Running the ArkAngel website

The website puts ArkAngel online:

- **Anyone** can browse missing-person profiles and send a tip, with no account needed.
- **Approved members** run case workspaces. Their tips, uploads and photos get correlated into ranked leads, and they can share a case with investigators.
- **Admins** approve accounts and approve every profile before it goes public.

It runs as two containers on any Linux server. The ArkAngel container runs the app, and the Caddy container gets and renews the HTTPS certificate and forwards traffic to the app. The app's port is never exposed to the internet.

## What you need

- A server with Docker installed: any $5-10/month VPS with 1 GB of RAM is plenty (Hetzner, DigitalOcean, Linode, ...).
- A domain or subdomain, e.g. `arkangel.example.org`, with a DNS **A record** pointing at the server's IP address.
- Ports 80 and 443 open on the server's firewall. Caddy needs both, to get the certificate and to serve the site.

## First start

```bash
git clone https://github.com/NullSecurityLLC/Ark_Angel.git
cd Ark_Angel
cp .env.example .env
nano .env                    # set DOMAIN (and ACME_EMAIL, SITE_NAME if you like)

docker compose up -d --build
docker compose run --rm app ark-angel web create-admin YOURNAME   # asks for a password twice
```

Open `https://YOUR-DOMAIN/` and sign in. Until an admin exists the site waits instead of serving pages. There's deliberately no first-run setup page, because whoever reached it first would own the site.

## Day to day

Most work happens in the browser under **Admin**:

- **Users:** approve or reject account requests, disable accounts, make someone an admin. Approve only people you can place: a family member, an agency, a known search group.
- **Profile reviews:** every profile, and every later edit to one, waits here. Before approving, check the police report reference is real (call the agency's non-emergency line if unsure). While an edit is in review, visitors keep seeing the last approved version.
- **All cases** and the **sign-in log**.

The same tasks from the server:

```bash
docker compose run --rm app ark-angel web users --pending
docker compose run --rm app ark-angel web approve USERNAME
docker compose run --rm app ark-angel web disable USERNAME
docker compose run --rm app ark-angel web passwd USERNAME    # also signs them out everywhere
```

## Backups

Everything lives in the `arkangel-data` volume: the site database, case evidence, uploaded files and profile photos.

```bash
docker compose exec app ark-angel web backup /data/backup.tar.gz
docker compose cp app:/data/backup.tar.gz ./arkangel-$(date +%F).tar.gz
docker compose exec app rm /data/backup.tar.gz
```

The backup is consistent even while the site is in use. **It contains tips and tipsters' contact details: keep it encrypted and off shared drives.** To restore one, stop the stack, unpack it into a fresh `arkangel-data` volume (`web.db`, `cases/`, `uploads/` and `media/` at the volume root), and start again.

## Updating

```bash
git pull
docker compose up -d --build
```

Data stays in the volume across updates.

## What protects it

- **Accounts.** There's no open sign-up: every account and every public profile is approved by an admin.
- **Sessions.** Sessions are server-side and expire after 30 minutes idle or 12 hours. Signing out, changing the password or disabling the account ends them.
- **Sign-in.** Passwords are hashed with scrypt. 5 failed sign-ins for a username, or 20 from one address, lock it for 15 minutes.
- **Forms.** Every form has a CSRF token. Cross-site posts and unexpected host names are refused.
- **Spam.** Tips and sign-ups are rate limited per visitor, using a keyed hash of their address that is never stored in readable form and is kept for 24 hours.
- **Photos.** Public profile photos are re-encoded with all metadata removed. Evidence photos keep their GPS for analysis and are only ever shown to the case team.
- **Browsers.** Pages run no scripts at all under a strict Content-Security-Policy, with HSTS, no framing and no referrers.
- **The container.** It runs as an unprivileged user on a read-only filesystem, with every Linux capability dropped.

## Running without Docker

Any machine with Python 3.10+:

```bash
pip install "ark-angel[web] @ git+https://github.com/NullSecurityLLC/Ark_Angel.git"
ark-angel web create-admin YOURNAME
ark-angel web serve                      # http://127.0.0.1:8765/, this computer only
```

To serve it publicly without Docker, put it behind your own HTTPS reverse proxy (Caddy, nginx) on the same machine. Leave it bound to `127.0.0.1`:

```bash
ark-angel web serve --behind-proxy --allow-host arkangel.example.org
```

It refuses to listen on a public address without `--behind-proxy`, so passwords never cross a network in plain HTTP.
