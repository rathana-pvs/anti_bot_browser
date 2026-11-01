# Companion app (bundled)

`agent.apk` is the signed release build of `android-agent/`, and `manifest.json` records its
version and SHA-256. Both are written by `android-agent/tools/bundle_release.sh` and committed
together. Phone setup (`backend/services/android_setup.py`) installs the APK only when its SHA-256
matches the manifest; until both files are here, setup stops at "Phone ready" and says so.
