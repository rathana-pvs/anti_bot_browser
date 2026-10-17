# Version 1.1.0

- Added Settings > App updates for checking, downloading, and installing signed whole-app releases.
- EasyOCR remains primary, with bounded Tesseract fallback for labels and unreadable timestamps.
- Improved first-post verification with optional author rows and explicit old-timestamp results.
- App installation waits for automation and live broadcasts to be idle.
- Runtime setup is checked separately after an app upgrade, preserving profiles and saved data.

The first updater-enabled version needs a one-time installer upgrade. Later
published stable releases can be installed from App updates. Dependency setup
may require administrator approval after restarting.
