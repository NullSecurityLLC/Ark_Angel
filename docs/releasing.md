# Releasing ArkAngel

Releases are built by `.github/workflows/release.yml`. Pushing a version tag
builds everything, smoke-tests it on Windows, macOS and Linux, and creates a
**draft** GitHub release. Nothing is public until someone publishes the draft.

## Steps

1. Bump `__version__` in `src/ark_angel/__init__.py` (the package version is read
   from there), update the version test in `tests/test_cli.py`, and merge to `main`.
2. Tag the merge commit and push the tag:

   ```bash
   git checkout main && git pull
   git tag v0.2.0
   git push origin v0.2.0
   ```

   The workflow fails early if the tag doesn't match `__version__`.
   Prefer a signed tag (`git tag -s v0.3.0`, with an SSH or GPG key registered on GitHub), so the tag itself
   shows as Verified.
3. Wait for the **Release** workflow to go green. It signs build provenance for every file (GitHub artifact
   attestations, logged in Sigstore's public transparency log), checks each signature verifies, and attaches
   these files to a draft release:
   - `ark_angel-<version>-py3-none-any.whl` and `ark_angel-<version>.tar.gz`
   - `ark-angel-windows-x86_64.exe`, `ark-angel-macos-arm64`, `ark-angel-linux-x86_64`
   - `ark-angel-recorder.zip` (the browser extension)
   - `SHA256SUMS.txt`

   Spot-check one before publishing: `gh attestation verify ark-angel-linux-x86_64 --repo NullSecurityLLC/Ark_Angel`.
4. Open the draft on the Releases page, edit the generated notes (say what's new
   and repeat the "not code-signed" note from the README), and click **Publish**.

## Icons

`packaging/icons/` holds the app icon in every format: the SVG master, Windows
`.ico`, macOS `.icns` and a 512 px PNG. The HTML reports embed
`src/ark_angel/report/favicon.png`. The Windows executable carries the icon.
The macOS one doesn't, because PyInstaller only applies icons to `.app` bundles,
and a command-line executable isn't one. The `.icns` is ready for a future Mac
app or DMG. All of them are drawn by
`scripts/build_icons.py`, so change the geometry or colours there and re-run it:

```bash
python scripts/build_icons.py
```

## Building locally

```bash
python -m pip install build pyinstaller==6.22.3
python -m build                                   # wheel + sdist in dist/
pyinstaller --onefile --name ark-angel --collect-data ark_angel \
  --icon packaging/icons/ark-angel.ico packaging/ark_angel_entry.py   # .icns on macOS; Linux ignores icons
./dist/ark-angel demo
```

## Not done yet

- **Branch protection on `main`** (require pull requests and passing checks), set in the repository's
  settings. Together with signed tags, it means nobody can slip a commit into a release unnoticed.
- **Browser stores.** Publishing the Recorder to the Chrome Web Store and Firefox Add-ons under a verified
  publisher gives it one official extension ID to point people at. A cloned extension that reads every
  page an investigator visits is the most dangerous fake there could be.

- **Code signing.** Windows and macOS warn about unsigned executables. Fixing that
  needs an Authenticode certificate and an Apple Developer ID plus notarization,
  both paid and tied to the organization.
- **Intel Macs.** Only an Apple Silicon build is produced. Add a `macos-13` matrix
  entry while GitHub still offers Intel runners.
- **PyPI.** Publishing to PyPI needs a PyPI project with trusted publishing set up
  for this repository. Then add a `pypa/gh-action-pypi-publish` step to the
  release job.
