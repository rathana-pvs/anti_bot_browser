#!/usr/bin/env bash
# Builds the signed release APK and bundles it with the desktop app's backend.
# Needs the release key (keystore.properties or AGENT_KEYSTORE_* variables), JDK 17 and the
# Android SDK/NDK from README.md. Commit the APK and manifest.json together.
set -euo pipefail
cd "$(dirname "$0")/.."
./gradlew --no-daemon assembleRelease
apk=app/build/outputs/apk/release/app-release.apk
[ -f "$apk" ] || { echo "No signed release APK; is the release key configured?" >&2; exit 1; }
out=../backend/vendor/android-agent
mkdir -p "$out"
cp "$apk" "$out/agent.apk"
version_name=$(sed -n 's/.*versionName = "\(.*\)".*/\1/p' app/build.gradle.kts)
version_code=$(sed -n 's/.*versionCode = \([0-9]*\).*/\1/p' app/build.gradle.kts)
sha=$(sha256sum "$out/agent.apk" | cut -d' ' -f1)
printf '{\n  "version_name": "%s",\n  "version_code": %s,\n  "sha256": "%s"\n}\n' "$version_name" "$version_code" "$sha" > "$out/manifest.json"
echo "Bundled $version_name ($sha)"
