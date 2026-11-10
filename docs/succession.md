# If the operator goes down

ArkAngel holds cases that matter. A missing person's family is counting on them, and in a defender case someone's
safety depends on them. If the person who runs the site dies, ends up in hospital or just disappears, the work has
to go on.

## On the website: name a successor

On **Account > If you go down: succession**, name someone you trust: an approved member with two-factor sign-in on.
Then choose two periods:

- **Inactive for** 14, 30, 60 or 90 days. After that long with no sign-in or page load from you, a handover starts by
  itself. Your successor can also ask for it early, for example "they're in hospital". Asking early needs their
  password and a two-factor code.
- **Waiting period** of 2, 7, 14 or 30 days, starting when a handover starts. You're emailed right away. **Anything you
  do on the site cancels it**: signing in, or loading a page. You can also remove or change your successor at any
  time.

When the waiting period ends without you:

- Every case you own passes to your successor as owner. You stay on each one as an editor, so if you come back,
  nothing of yours is gone and they or an admin can make you owner again.
- If you were a site admin, your successor becomes one too, so the site is never left with nobody who can approve
  accounts and profiles.
- Both of you, and the logs, record what happened. The emails say who and when, never anything about a case.

`ark-angel web successions` lists every designation and any handover under way.

**The risk to weigh:** if someone took over your successor's account, they could ask for a handover early and wait
you out. That's why successors must have two-factor sign-in on, why asking early re-checks their password and code,
and why you get an email straight away. Pick a waiting period long enough that you'd see that email in time.

## The server itself: write it down now

The handover above covers the website. If you run the server, your successor also needs what lives outside it.
Put this in a password manager's emergency-access feature, or a sealed envelope with someone you trust:

- **The hosting account** (VPS provider or similar) and its two-factor recovery codes.
- **The domain registrar** and the DNS login. Without them the site's address lapses.
- **The backup decryption key** (the age private key from `age-keygen`). By design it isn't on the server, so without
  it nobody can open the backups, including your successor. See `docs/deploy.md`.
- **The backup storage** (rclone remote) login, and the `.env` settings: `DOMAIN`, the email (`SMTP_*`) settings and
  `BACKUP_*`.
- **A short note**: which cases are urgent, who the families and agencies are, and who to tell.

Check it once a year, and whenever any of those change.
