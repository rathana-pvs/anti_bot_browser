# App releases and in-app updates

Normal commits and pushes are development. Only a deliberate `v*` tag builds a
release, and even then the workflow creates a **draft**, not a published update.

## One-time signing setup

The app embeds a public verification key in `manager-app/src-tauri/tauri.conf.json`.
Its matching private key was generated locally at:

`~/.config/isolated-browser-manager/release-signing.key`

Back it up securely. Never commit it, attach it to an issue, or put it in the
application bundle. Set the complete file contents as the GitHub Actions secret
`TAURI_SIGNING_PRIVATE_KEY` in this repository's Settings > Secrets and variables
> Actions. Set `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` only if the key has a password
(the generated key currently has none). The workflow fails if the key is missing
or the resulting signatures do not verify with the app's embedded public key.

Do not generate a new key for each release: existing apps trust the embedded
public key. Key rotation requires an explicit migration. The repository is
currently public, so no GitHub access token is shipped to users.

## Prepare a release

1. Finish and test the desired commits. Update `RELEASE_NOTES.md`.
2. Run `node scripts/release-version.mjs set 1.1.1` (use the desired higher
   version). VERSION is authoritative; this updates npm, Tauri, Cargo, and both
   lockfiles together. The initial updater-enabled version is 1.1.0, higher than
   the earlier installed 1.0.19 as well as the later source label 1.0.2.
3. Run `npm test` and `npm run build` in manager-app, `cargo test --locked` in
   manager-app/src-tauri, and the relevant Python tests.
4. Commit and merge the release changes to main. Push main normally; no release
   is created. Then deliberately tag that tested commit and push the tag:

```bash
git tag -a v1.1.1 -m "Release 1.1.1"
git push origin v1.1.1
```

The workflow validates tag/version consistency and increasing versions, builds
Windows NSIS/MSI and Linux Debian/AppImage packages, signs them with their version,
verifies all signatures, and assembles **one** complete `latest.json`. Both
platforms must succeed before draft creation. Assets include installers, `.sig`
files, update metadata, and `SHA256SUMS`. A retry may refresh a draft; a published
release cannot be overwritten by this workflow. Manual workflow dispatch creates
downloadable CI artifacts only, not a GitHub Release.

## Publish deliberately

Open the draft in GitHub Releases. Download and test the actual installers,
including a previous-version upgrade, idle/busy installation, authentication
cancellation, dependency setup, backend startup, and preservation of profiles and
Brain selections. Test Windows/WSL on Windows; a Linux unit test is not a substitute.

For stable publication, uncheck **Set as a pre-release**, choose **Set as the latest
release**, and click **Publish release**. This is the step that makes the app
offer the update. A version with a suffix (for example `1.2.0-beta.1`) creates a
prerelease draft. Beta releases remain manual downloads; this first implementation
has only the stable in-app channel. Do not label a beta as the stable latest release.

## In the app

Settings > App updates checks the published stable `latest.json`, displays the
version/notes, and downloads the matching artifact. Downloads are verified against
the embedded key and signed version before installation. Users explicitly choose
**Install and restart**; an update never installs silently during posting.

Install commands reuse the idle checks for automation/live activity and the setup
exclusion flag. A loopback-only, authenticated maintenance handshake atomically
blocks new container ownership claims while idle installation runs. Scheduled
work defers without changing its queue state. A failed install releases the
in-memory pause; backend restart also clears it. Independently managed workers
are never stopped by the updater.
An installation failure keeps the verified download available for retry and tries
to restart the desktop-owned backend. Native Linux `.deb` support uses the Tauri
updater 2.14 package installer (administrator approval); AppImage and Windows use
their matching native installer. No arbitrary download URL or installer path can
be supplied through the frontend commands.

After restart, completed dependency setup is tracked in
`data/runtime-installed-version`. If it does not match the bundled VERSION, Setup
Center requests install/repair and the desktop defers automation service startup.
The marker is written atomically only after the installer's verification passes.
Code copy alone cannot claim dependency compatibility. User data and Brain choices
remain outside the bundled code payload.

Older installed versions have no updater, so 1.1.0 requires one manual installer
upgrade. This implementation does not publish 1.1.0 automatically or change the
currently installed desktop app. A real published signed release is required to
validate the complete end-to-end update flow.
