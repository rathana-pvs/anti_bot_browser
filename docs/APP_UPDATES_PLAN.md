# Whole-app releases and updates

1. Use VERSION as the release source of truth. Synchronize npm/Tauri/Cargo
   manifests and lockfiles with one command; reject mismatched or non-increasing
   tagged releases. Ordinary commits and pushes never publish a release.
2. Build both platforms before assembling one draft GitHub Release. Include
   signed updater artifacts, a signed Debian package, release notes, and one
   complete latest.json. Manual builds upload artifacts only. Stable publication
   is an explicit human action after installer testing.
3. Add Settings > App updates, with current version, check, notes, download
   progress, and install/restart. Use the Tauri updater verification code behind
   guarded Rust commands. Support Windows installers, Linux AppImage, and the
   existing Debian installation via a narrowly scoped package-manager invocation.
4. Downloads can happen while work runs. Installation requires an idle,
   desktop-owned backend and excludes concurrent setup/update operations. Never
   interrupt a separately managed worker. Freeze new execution admission atomically
   with ownership claims, and resume the backend/admission if installation fails.
5. Track completed runtime setup separately from bundled code version. After an
   app update, show Setup Center when dependencies need migration; mark completion
   only after successful setup. Preserve profiles, media, queues, and Brain choices.
6. Test versioning/release metadata, idle gating, updater target selection,
   missing configuration, cancellation/failure cleanup, frontend build, and Rust
   compilation/tests. Actual cross-version installation requires published signed
   releases and is not replaced by mocked tests.

The sibling main-server context files are absent. This change is local to the
desktop and release tooling, with no shared-protocol or cloud-job changes.

## Implementation validation

- Frontend/release-tool tests: 65 passed; TypeScript/Vite production build passed.
- Rust tests: 7 passed, including exclusion guards and runtime-version tracking.
- Targeted backend admission, cleanup, queue, payload, and worker integration:
  49 passed. Automation tests separately: 466 passed plus 6 subtests.
- Final Linux 1.1.0 Debian package built and its updater signature and signed
  version verified with minisign. Four signed test fixtures produced the complete
  cross-platform manifest; these fixtures are not distributable installers.
- Staged payload privacy scan passed. Version/tag checks and diff checks passed.
- The combined Python suite is not clean: 843 passed, 18 failed, 7 skipped.
  The same failures reproduce on unchanged main (container shutdown-state leakage
  and a warming-options test). No unrelated test fixes are included here.

Windows/WSL and AppImage packaging remain to be exercised by CI, and a real
published-release upgrade remains untested. Configure the signing secret and test
both platform installers before publishing. No release was pushed or published,
and the locally installed desktop application was not changed.
