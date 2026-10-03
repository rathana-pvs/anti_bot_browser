#!/usr/bin/env python3
"""Serve a one-shot, localhost-only browser environment observation page."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PROBE_SCRIPT = r"""
async function collectEnvironment() {
  const report = {
    observed_at: new Date().toISOString(),
    user_agent: navigator.userAgent || null,
    platform: navigator.platform || null,
    language: navigator.language || null,
    languages: Array.from(navigator.languages || []),
    hardware_concurrency: navigator.hardwareConcurrency || null,
    device_memory: navigator.deviceMemory || null,
    max_touch_points: navigator.maxTouchPoints || 0,
    webdriver: navigator.webdriver === true,
    cookie_enabled: navigator.cookieEnabled,
    do_not_track: navigator.doNotTrack || null,
    plugin_count: navigator.plugins ? navigator.plugins.length : null,
    mime_type_count: navigator.mimeTypes ? navigator.mimeTypes.length : null,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || null,
    timezone_offset_minutes: new Date().getTimezoneOffset(),
    screen: {
      width: screen.width,
      height: screen.height,
      avail_width: screen.availWidth,
      avail_height: screen.availHeight,
      color_depth: screen.colorDepth,
      pixel_depth: screen.pixelDepth,
      device_pixel_ratio: window.devicePixelRatio,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight
    },
    webgl: null,
    user_agent_data: null
  };

  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (gl) {
      const debug = gl.getExtension('WEBGL_debug_renderer_info');
      report.webgl = {
        vendor: gl.getParameter(gl.VENDOR),
        renderer: gl.getParameter(gl.RENDERER),
        version: gl.getParameter(gl.VERSION),
        shading_language_version: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
        unmasked_vendor: debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : null,
        unmasked_renderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : null
      };
    }
  } catch (error) {
    report.webgl = {error: String(error)};
  }

  try {
    if (navigator.userAgentData) {
      const highEntropy = await navigator.userAgentData.getHighEntropyValues([
        'architecture', 'bitness', 'fullVersionList', 'model',
        'platformVersion', 'wow64'
      ]);
      report.user_agent_data = {
        brands: navigator.userAgentData.brands,
        mobile: navigator.userAgentData.mobile,
        platform: navigator.userAgentData.platform,
        ...highEntropy
      };
    }
  } catch (error) {
    report.user_agent_data = {error: String(error)};
  }

  return report;
}

(async () => {
  let sent = false;
  try {
    const report = await collectEnvironment();
    const response = await fetch('/report', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(report)
    });
    sent = response.ok;
  } catch (_) {}
  document.getElementById('status').textContent = sent
    ? 'Browser environment recorded. Continuing…'
    : 'Browser environment could not be recorded. Continuing…';
  window.setTimeout(() => window.location.replace(__TARGET_URL__), 100);
})();
"""


def atomic_write_json(path: str, value: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="browser-observation-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def make_handler(output_path: str, target_url: str):
    encoded_target = json.dumps(target_url).replace("<", "\\u003c")
    script = PROBE_SCRIPT.replace("__TARGET_URL__", encoded_target)
    page = (
        "<!doctype html><meta charset='utf-8'><title>Browser environment check</title>"
        "<body><p id='status'>Recording browser environment…</p>"
        f"<script>{script}</script></body>"
    ).encode("utf-8")

    class ProbeHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            if self.path != "/report":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 131072:
                self.send_error(400)
                return
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("report must be an object")
                atomic_write_json(output_path, value)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, OSError):
                self.send_error(400)
                return
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    return ProbeHandler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/run/browser-observation.json")
    parser.add_argument("--target", default="https://www.google.com")
    parser.add_argument("--port", type=int, default=9223)
    args = parser.parse_args()
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        make_handler(args.output, args.target),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
