# Version 1.1.4

- Choose the primary OCR engine beside the manager's global resource controls: V5 + Tesseract (default), V6 + Tesseract (speed), or EasyOCR + Tesseract (legacy/generic).
- Save the OCR choice across launches. Restart the app/backend to apply a change; active jobs keep their startup engine.
- Use the selected engine consistently in the shared worker and local fallback, with separate model caches and engine provenance in telemetry.
- Install verified, pinned PaddleOCR models during setup and use local assets during recognition. Runtime repair is required after upgrading to install the new dependencies.
- Recognize `about a minute ago` as a one-minute timestamp, including attributed post headers. Old and conflicting timestamps remain rejected.
- Include the existing reliability improvements and signed Settings > App updates flow from the 1.1.3 source build.

Linux validation: 918 Python/evaluation tests passed (7 skipped), 68 frontend tests passed, 8 desktop tests passed, and the production frontend/desktop build succeeded. The installed Linux app passed selection persistence checks and CPU smoke checks for all three OCR engines. The timestamp suite passed in the installed runtime. These checks do not establish full publication-workflow success or manual Windows/WSL installer validation.

After installing, use Settings > Setup and repair to update the runtime when prompted. Existing profiles, media, queues, and Brain selections remain in persistent app storage.
