# ArkAngel Recorder (browser extension)

Logs your investigation browsing into an ArkAngel case, so you don't have to.
Pages, searches and anything you right-click → **Add selection to ArkAngel
case** go to `ark-angel browse` running on your own computer. Pages that
mention a phone, email, handle or plate already in the case get flagged while
you're still on them.

Works in Chrome, Edge, Brave, Opera, Vivaldi and Firefox 121+.

## Install

**Only install it from an official ArkAngel release.** An extension like this one can read every
page you visit, so a look-alike is dangerous. Check the zip before you load it:

```bash
gh attestation verify ark-angel-recorder.zip --repo NullSecurityLLC/Ark_Angel
```

It isn't in the browser stores yet, so load it unpacked. Use the
`ark-angel-recorder.zip` from a [release](https://github.com/NullSecurityLLC/Ark_Angel/releases)
(unzip it), or this `extension/` folder from a clone.

- **Chrome / Edge / Brave**: open `chrome://extensions` (`edge://extensions`),
  turn on *Developer mode*, click *Load unpacked*, pick the folder.
- **Firefox**: open `about:debugging#/runtime/this-firefox`, click *Load
  Temporary Add-on*, pick `manifest.json`. Temporary add-ons are removed when
  Firefox restarts. On first Record, allow it access to sites when asked.

## Use

Pair it with one of these:

- **A case on your ArkAngel website**: open the case, go to the **Recorder** tab,
  and press *Make token*. Captures go straight into that case, under your name,
  and its **Trail** tab shows them.
- **This computer**: run `ark-angel browse C1`. The token is kept between runs
  (`--new-token` replaces it), so you only pair once.

Either one shows an address and a token. Paste both into the extension
(*Pairing and privacy*), press **Save and test**, then **Record**.

Ctrl+C stops `ark-angel browse` and re-runs `enrich`. Then:

```bash
ark-angel trail C1      # sessions, pivots, threads you followed, loose ends
ark-angel report C1     # pages that corroborate the case now count in the leads
```

## What it sends, and what it doesn't

- Only while **Record** is on. A right-click "Add to case" is the one
  exception, because you asked for it.
- Never from private/incognito windows. The manifest sets
  `"incognito": "not_allowed"`, and it checks again before every capture.
- Never from sites on the skip list. The defaults cover webmail, sign-in pages,
  banks, PayPal and password managers. **Skip this site** adds the current one.
- Only to the address you paired with: `http://127.0.0.1:8766` for
  `ark-angel browse`, or your ArkAngel website. Nothing goes to any other server,
  and pages on the ArkAngel site itself are never recorded.
- For each page it sends the URL, title, time and visible text, so
  `ark-angel` can find phones, handles and plates on it. Untick *Read page
  text* to send only URL and title. Only snippets around what it found are kept
  in the case, not the whole page.
- If ArkAngel isn't reachable, or the site's rate limit kicks in, captures
  wait in the extension (up to 300) and go out when it is. The badge shows `!` while any are waiting.

## Badge

`REC` while recording. A number is how many pages matched the case. `!`
means captures are waiting to be sent.
