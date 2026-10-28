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

## Email notifications

Optional, but without it nobody hears about new account requests, profiles waiting for review, or new tips
unless they go and look. Fill in the `SMTP_*` lines in `.env` with any email provider's SMTP settings
(Postmark, Mailgun, Amazon SES, Fastmail, Google Workspace...). Then:

```bash
docker compose up -d                                              # restart with the new settings
docker compose run --rm app ark-angel web test-email you@example.org
```

Admins get alerts at the address on their account (set it on the **Account** page, or with
`web create-admin NAME --email ...`), plus any address in `ADMIN_EMAIL`. Members set their own address and
can turn tip emails off.

| Who | Gets an email when | Throttle |
|---|---|---|
| Admins | someone requests an account; a profile is waiting for review | at most one of each kind per 10 minutes |
| A new member | their account is approved | |
| A case owner | their profile is approved or sent back | |
| Everyone on a case | a tip arrives (unless they turned it off) | at most one per case per 15 minutes |

**No email contains tip text, tippers' contact details, names or case titles**: only that something happened,
and a link to the page behind the sign-in. Email passes through providers and shows on lock screens. Mail goes
out in the background, so a slow mail server never holds up a tip. TLS is required (STARTTLS on 587 or TLS on
465, with the certificate checked); plain SMTP is only accepted for a relay on the same machine.

## Backups

The `backup` service backs everything up every night at 03:15 UTC: the site database, case evidence,
uploaded files and profile photos. It encrypts the backup, copies it off the server, and keeps 30 days
there and the last 7 copies on the server. It mounts the site's data read-only.

**1. Make a key, on your own computer, not the server.** Install [age](https://age-encryption.org), then:

```bash
age-keygen -o arkangel-backup-key.txt
```

It prints a `Public key: age1...`. Put that in `.env` as `BACKUP_RECIPIENT`. Keep
`arkangel-backup-key.txt` somewhere safe and **off the server**, such as a password manager or an encrypted
USB stick, ideally in two places. The server can only lock backups, never open them, so neither the storage
company nor someone who breaks into the server can read them. **Lose the key and no one can open the
backups, including you.**

**2. Pick where they go.** Any [rclone](https://rclone.org/overview/) remote works. Set it up with
`RCLONE_CONFIG_<NAME>_*` lines in `.env` and point `BACKUP_DEST` at it. Backblaze B2, for example (create
a bucket and an application key limited to it):

```bash
BACKUP_DEST=offsite:my-arkangel-backups/nightly
RCLONE_CONFIG_OFFSITE_TYPE=b2
RCLONE_CONFIG_OFFSITE_ACCOUNT=<keyID>
RCLONE_CONFIG_OFFSITE_KEY=<applicationKey>
```

S3, Cloudflare R2 (`TYPE=s3`, `PROVIDER=Cloudflare`), Wasabi and SFTP work the same way. Use a backend that
logs in with a key. OAuth ones like Google Drive need a token the read-only container can't save.

**3. Turn it on and test it:**

```bash
docker compose up -d
docker compose exec backup ark-angel web backup-nightly --once     # one backup now
docker compose logs backup                                         # "backup ok: ... copied to offsite:..."
```

If a backup fails, the admins get an email (set up email notifications first). For a dead man's switch, set
`BACKUP_PING_URL` to a [healthchecks.io](https://healthchecks.io) check URL. It's hit after every good
backup, and healthchecks emails you if a day goes by without one, even if the whole server is down. Until
`BACKUP_RECIPIENT` is set, the backup service logs a warning and emails the admins every day. The site
itself runs either way.

### Restoring

Do a practice restore once, before you need it. On your own computer, download a backup from your storage
(or `docker compose cp backup:/backups/<file> .`). Then decrypt it and pipe it into the server:

```bash
ssh you@server 'cd Ark_Angel && docker compose stop app'
age -d -i arkangel-backup-key.txt arkangel-20261027-031500.tar.gz.age \
  | ssh you@server 'cd Ark_Angel && docker compose run --rm -T app ark-angel web restore - --force'
ssh you@server 'cd Ark_Angel && docker compose start app'
```

The key never leaves your computer. The site's current data isn't deleted: it's moved to
`/data/pre-restore-<time>/` in the data volume. `web restore` checks every file in the archive first and
refuses anything that would land outside the data folder.

For a one-off copy by hand: `docker compose exec app ark-angel web backup /data/manual.tar.gz --encrypt-to age1...`.

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
