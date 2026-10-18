# Version 1.1.3

- Combined the reliability/design-system changes with signed in-app updates.
- Restored Settings > App updates in the combined desktop build.

- Added Settings > App updates for checking, downloading, and installing signed whole-app releases.
- Missing published update information now shows a clear informational message instead of a technical JSON error. Connection and invalid-metadata errors remain errors.
- EasyOCR remains primary, with bounded Tesseract fallback for labels and unreadable timestamps.
- Improved first-post verification with optional author rows and explicit old-timestamp results.
- App installation waits for automation and live broadcasts to be idle.
- Runtime setup is checked separately after an app upgrade, preserving profiles and saved data.

The first updater-enabled version needs a one-time installer upgrade. Later
published stable releases can be installed from App updates. Dependency setup
may require administrator approval after restarting.
