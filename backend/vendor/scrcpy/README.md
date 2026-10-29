# scrcpy-server

`scrcpy-server-v3.3.4` is the unmodified server from the official release
<https://github.com/Genymobile/scrcpy/releases/tag/v3.3.4>
(SHA-256 `8588238c9a5a00aa542906b6ec7e6d5541d9ffb9b5d0f6e1bc0e365e2303079e`,
also pinned in `backend/services/android_live.py`).

scrcpy is Copyright (C) Genymobile and Romain Vimont, licensed under the
Apache License 2.0: <https://github.com/Genymobile/scrcpy/blob/master/LICENSE>.

It is pushed to the phone for the live view. Keep 3.3.3 or newer: 3.3.1 crashes
on Android 17 (`IDisplayWindowListener.onDisplayAnimationsDisabledChanged`).
Version 4.0 changed the video stream format, so upgrading past 3.x needs
changes to `android_live.py`.
