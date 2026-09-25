"""Vision Engine for Element Localization.

Implements the 3-Tier Hybrid Locator:
  Tier 1: Position Cache (0ms)
  Tier 2: OpenCV Multi-Scale Template Matching (10–30ms)
  Tier 3: Intelligent Fallback & Coordinate Learning
"""

import json
import hashlib
import os
import re
import time
from datetime import datetime, timezone
import cv2
import numpy as np
from .container_client import ContainerClient

try:
    from skimage.metrics import structural_similarity
except ImportError:  # Optional until dependencies are installed.
    structural_similarity = None


TEMPLATE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "templates")
)

# Canonical normalized regions: (x1_norm, y1_norm, x2_norm, y2_norm)
NORMALIZED_REGIONS = {
    "bottom_action_bar": (0.00, 0.68, 1.00, 1.00),
    "modal_header_alerts": (0.18, 0.03, 0.82, 0.30),
    "reel_sidebar": (0.58, 0.10, 1.00, 1.00),
    "profile_post_stream": (0.12, 0.20, 0.88, 1.00),
    "composer_modal": (0.20, 0.15, 0.80, 0.85),
    "feed_composer": (0.20, 0.10, 0.80, 0.55),
}


class VisionEngine:
    NORMALIZED_REGIONS = NORMALIZED_REGIONS
    CACHE_TTL_HOURS = 72
    MAX_CONSECUTIVE_FAILURES = 3
    OCR_CACHE_MAX_ENTRIES = 24
    _ocr_readers: dict[tuple[str, ...], object] = {}

    def __init__(self, client: ContainerClient):
        self.client = client
        self.cache_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "profiles", client.profile_id)
        )
        self.cache_file = os.path.join(self.cache_dir, "element_cache.json")
        self._cache = self._load_cache()
        self.telemetry = None
        self.theme = "unknown"
        self.theme_confidence = 0.0
        self.configured_resolution = None
        self.locale = "unknown"
        self.browser_zoom = None
        self._ocr_cache: dict[tuple, list[dict]] = {}

    def set_runtime_context(
        self,
        configured_resolution: str | None = None,
        locale: str | None = None,
        browser_zoom: float | None = None,
    ) -> None:
        """Set stable context used to isolate locator and OCR caches."""
        if configured_resolution:
            self.configured_resolution = str(configured_resolution)
        if locale:
            self.locale = str(locale)
        if browser_zoom is not None:
            self.browser_zoom = float(browser_zoom)

    def _context_signature(self) -> str:
        zoom = "auto" if self.browser_zoom is None else str(round(self.browser_zoom, 3))
        return "|".join((
            self.theme or "unknown",
            self.configured_resolution or "unknown",
            self.locale or "unknown",
            zoom,
        ))

    def _element_cache_key(self, label: str) -> str:
        signature = self._context_signature()
        if signature == "unknown|unknown|unknown|auto":
            return label
        return f"{label}@@{signature}"

    @staticmethod
    def infer_theme(screen: np.ndarray | None) -> tuple[str, float]:
        """Infer Facebook's light/dark theme from the page viewport."""
        if screen is None or not getattr(screen, "size", 0):
            return "unknown", 0.0
        height, width = screen.shape[:2]
        # Exclude browser chrome and narrow outer edges. These areas do not
        # represent Facebook's active theme and can skew small screenshots.
        viewport = screen[
            int(height * 0.10) : max(int(height * 0.10) + 1, int(height * 0.95)),
            int(width * 0.05) : max(int(width * 0.05) + 1, int(width * 0.95)),
        ]
        if viewport.size == 0:
            return "unknown", 0.0
        gray = cv2.cvtColor(viewport, cv2.COLOR_BGR2GRAY)
        dark_fraction = float(np.mean(gray < 105))
        light_fraction = float(np.mean(gray > 170))
        if dark_fraction >= 0.55:
            return "dark", round(min(1.0, dark_fraction), 3)
        if light_fraction >= 0.55:
            return "light", round(min(1.0, light_fraction), 3)
        return "unknown", round(max(dark_fraction, light_fraction), 3)

    def detect_theme(self, screen: np.ndarray | None = None) -> tuple[str, float]:
        """Detect and cache theme before theme-sensitive visual work begins."""
        screen = self.capture_screen() if screen is None else screen
        theme, confidence = self.infer_theme(screen)
        self.theme = theme
        self.theme_confidence = confidence
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            telemetry.observe_screen(screen)
            telemetry.set_environment(theme=theme, theme_confidence=confidence)
        return theme, confidence

    @staticmethod
    def _content_digest(image: np.ndarray) -> str:
        contiguous = np.ascontiguousarray(image)
        return hashlib.blake2b(memoryview(contiguous), digest_size=8).hexdigest()

    def _prepare_ocr_image(self, crop: np.ndarray, targeted: bool) -> tuple[np.ndarray, float]:
        """Resize and lightly normalize OCR input for the detected visual theme."""
        max_dimension = max(crop.shape[:2])
        # Keep inference bounded at the measured 1280px ceiling. Targeted
        # regions preserve detail naturally because their crops are smaller.
        dimension_cap = 1280.0
        scale = min(1.0, dimension_cap / max_dimension)
        prepared = crop
        if scale < 1.0:
            prepared = cv2.resize(
                crop,
                (int(crop.shape[1] * scale), int(crop.shape[0] * scale)),
                interpolation=cv2.INTER_AREA,
            )
        if self.theme == "dark" and targeted:
            # Mild luminance-only CLAHE raises low-contrast dark-mode text while
            # retaining Facebook's color geometry for EasyOCR detection.
            lab = cv2.cvtColor(prepared, cv2.COLOR_BGR2LAB)
            luminance, channel_a, channel_b = cv2.split(lab)
            enhanced_luminance = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8)).apply(luminance)
            enhanced = cv2.cvtColor(
                cv2.merge((enhanced_luminance, channel_a, channel_b)),
                cv2.COLOR_LAB2BGR,
            )
            prepared = cv2.addWeighted(prepared, 0.35, enhanced, 0.65, 0)
        return prepared, scale

    @staticmethod
    def _region_name(region) -> str:
        if region is None:
            return "full_screen"
        if isinstance(region, str):
            return region
        if isinstance(region, (tuple, list)) and len(region) == 4:
            values = ",".join(str(round(float(value), 4)) for value in region)
            return f"region:{values}"
        return "custom_region"

    def _record_locator(self, **event) -> None:
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            telemetry.record_locator(**event)

    def capture_screen(self) -> np.ndarray:
        """Capture the current screen through the configured container client."""
        return self.client.screenshot()

    @classmethod
    def _get_ocr_reader(cls, languages: tuple[str, ...] = ("en",)):
        """Lazily initialize one pretrained EasyOCR reader per language set with 4-thread CPU tuning and warmup."""
        if languages in cls._ocr_readers:
            return cls._ocr_readers[languages]
        try:
            import warnings
            warnings.filterwarnings(
                "ignore",
                category=UserWarning,
                message=r".*torch\.quantize_per_tensor.*deprecated.*",
            )
            import torch
            torch.set_num_threads(4)
            import easyocr

            model_dir = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "models", "easyocr")
            )
            os.makedirs(model_dir, exist_ok=True)
            reader = easyocr.Reader(
                list(languages),
                gpu=False,
                verbose=False,
                model_storage_directory=model_dir,
                download_enabled=False,
            )
            # Warm up detector and recognizer with a minimal dummy image to eliminate cold-start lag
            dummy = np.zeros((64, 128, 3), dtype=np.uint8)
            reader.readtext(dummy)
        except Exception as exc:
            print(f"Warning: EasyOCR is unavailable: {exc}")
            reader = None
        cls._ocr_readers[languages] = reader
        return reader

    @staticmethod
    def get_pixel_region(
        screen_shape: tuple[int, ...],
        normalized_box: tuple[float, float, float, float] | str,
    ) -> tuple[int, int, int, int]:
        """
        Convert normalized (x1_norm, y1_norm, x2_norm, y2_norm) into (x, y, width, height) pixels
        based on the screen's actual dimensions (height, width).
        """
        if isinstance(normalized_box, str):
            normalized_box = NORMALIZED_REGIONS.get(normalized_box.strip().lower(), (0.0, 0.0, 1.0, 1.0))
        sh, sw = screen_shape[:2]
        x1_norm, y1_norm, x2_norm, y2_norm = normalized_box
        x1 = int(round(max(0.0, min(1.0, x1_norm)) * sw))
        y1 = int(round(max(0.0, min(1.0, y1_norm)) * sh))
        x2 = int(round(max(0.0, min(1.0, x2_norm)) * sw))
        y2 = int(round(max(0.0, min(1.0, y2_norm)) * sh))
        return (x1, y1, max(1, x2 - x1), max(1, y2 - y1))

    @staticmethod
    def expand_region(
        screen_shape: tuple[int, ...],
        pixel_region: tuple[int, int, int, int],
        ratio: float = 0.20,
    ) -> tuple[int, int, int, int]:
        """Expand a pixel region by ratio (e.g. 20%) in all directions, clipped to screen boundaries."""
        sh, sw = screen_shape[:2]
        x, y, w, h = pixel_region
        pad_x = int(w * ratio)
        pad_y = int(h * ratio)
        new_x = max(0, x - pad_x)
        new_y = max(0, y - pad_y)
        new_w = min(sw - new_x, w + (pad_x * 2))
        new_h = min(sh - new_y, h + (pad_y * 2))
        return (new_x, new_y, new_w, new_h)

    @staticmethod
    def _crop_region(
        screen: np.ndarray,
        region: tuple[int, int, int, int] | tuple[float, float, float, float] | str | None,
    ):
        if region is None:
            return screen, 0, 0
        if isinstance(region, str):
            region = VisionEngine.get_pixel_region(screen.shape, region)
        elif (
            isinstance(region, (tuple, list))
            and len(region) == 4
            and all(isinstance(v, (float, int)) and 0.0 <= v <= 1.0 for v in region)
            and any(isinstance(v, float) for v in region)
        ):
            region = VisionEngine.get_pixel_region(screen.shape, region)

        x, y, width, height = region
        sh, sw = screen.shape[:2]
        x = max(0, min(sw, int(x)))
        y = max(0, min(sh, int(y)))
        width = max(0, min(sw - x, int(width)))
        height = max(0, min(sh - y, int(height)))
        return screen[y : y + height, x : x + width], x, y

    def read_text(
        self,
        screen: np.ndarray | None = None,
        region: tuple[int, int, int, int] | tuple[float, float, float, float] | str | None = None,
        min_confidence: float = 0.35,
        languages: tuple[str, ...] = ("en",),
    ) -> list[dict]:
        """Return OCR text, confidence, bounds, and center in screen coordinates."""
        started = time.perf_counter()
        region_name = self._region_name(region)
        screen = self.capture_screen() if screen is None else screen
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            telemetry.observe_screen(screen)
        crop, offset_x, offset_y = self._crop_region(screen, region)
        if crop.size == 0:
            if telemetry is not None:
                telemetry.record_ocr((time.perf_counter() - started) * 1000.0, region_name, 0, outcome="empty_region")
            return []
        cache_key = (
            self._content_digest(crop),
            crop.shape,
            int(offset_x),
            int(offset_y),
            tuple(languages),
            self._context_signature(),
        )
        cached_results = self._ocr_cache.get(cache_key)
        if cached_results is not None:
            results = [dict(item) for item in cached_results if item["confidence"] >= min_confidence]
            if telemetry is not None:
                telemetry.record_ocr(
                    (time.perf_counter() - started) * 1000.0,
                    region_name,
                    len(results),
                    [item["confidence"] for item in results],
                    outcome="cache_hit",
                )
            return results

        ocr_image, scale = self._prepare_ocr_image(crop, targeted=region is not None)
        reader = self._get_ocr_reader(languages)
        if reader is None:
            if telemetry is not None:
                telemetry.record_ocr((time.perf_counter() - started) * 1000.0, region_name, 0, outcome="reader_unavailable")
            return []

        raw_items = []
        try:
            raw_results = reader.readtext(ocr_image, detail=1, paragraph=False)
        except Exception as exc:
            print(f"Warning: OCR failed: {exc}")
            if telemetry is not None:
                telemetry.record_ocr((time.perf_counter() - started) * 1000.0, region_name, 0, outcome="error")
            return []

        for box, text, confidence in raw_results:
            confidence = float(confidence)
            xs = [int(point[0] / scale) + offset_x for point in box]
            ys = [int(point[1] / scale) + offset_y for point in box]
            bounds = (min(xs), min(ys), max(xs), max(ys))
            raw_items.append(
                {
                    "text": str(text).strip(),
                    "confidence": confidence,
                    "bounds": bounds,
                    "center": ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2),
                }
            )
        self._ocr_cache[cache_key] = raw_items
        while len(self._ocr_cache) > self.OCR_CACHE_MAX_ENTRIES:
            self._ocr_cache.pop(next(iter(self._ocr_cache)))
        results = [dict(item) for item in raw_items if item["confidence"] >= min_confidence]
        if telemetry is not None:
            telemetry.record_ocr(
                (time.perf_counter() - started) * 1000.0,
                region_name,
                len(results),
                [item["confidence"] for item in results],
            )
        return results

    def find_text(
        self,
        labels: str | tuple[str, ...] | list[str],
        screen: np.ndarray | None = None,
        region: tuple[int, int, int, int] | tuple[float, float, float, float] | str | None = None,
        min_confidence: float = 0.45,
        prefer_lower_half: bool = False,
    ) -> dict | None:
        """Find an OCR label and return the highest-confidence matching result."""
        screen = self.capture_screen() if screen is None else screen
        if prefer_lower_half and region is None:
            region = (0, screen.shape[0] // 2, screen.shape[1], screen.shape[0] // 2)
        wanted = (labels,) if isinstance(labels, str) else tuple(labels)
        normalized = tuple(label.casefold().strip() for label in wanted)
        candidates = []
        for item in self.read_text(screen, region=region, min_confidence=min_confidence):
            value = item["text"].casefold().strip()
            if any(value == label or label in value for label in normalized):
                if prefer_lower_half and item["center"][1] < screen.shape[0] // 2:
                    continue
                candidates.append(item)
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item["confidence"], item["center"][1]), reverse=True)
        return candidates[0]

    def find_text_cascaded(
        self,
        labels: str | tuple[str, ...] | list[str],
        region: str | tuple[float, float, float, float] | tuple[int, int, int, int] | None = None,
        screen: np.ndarray | None = None,
        min_confidence: float = 0.45,
        prefer_lower_half: bool = False,
        expand_ratio: float = 0.20,
    ) -> dict | None:
        """
        Find an OCR label using a 3-tier cascade:
          Tier 1: Target region search (localized crop, ~420ms on host).
          Tier 2: Expanded region (+20-25%) search to catch slight layout shifts.
          Tier 3: Measured full-screen fallback search.
        """
        started = time.perf_counter()
        screen = self.capture_screen() if screen is None else screen
        locator_name = "text:" + "|".join((labels,) if isinstance(labels, str) else labels)
        region_name = self._region_name(region)
        if screen is None or screen.size == 0:
            self._record_locator(
                locator=locator_name, tier="screen_unavailable", region=region_name,
                duration_ms=(time.perf_counter() - started) * 1000.0, found=False,
                fallback_reason="screen_unavailable",
            )
            return None

        if region is not None:
            if isinstance(region, str):
                pixel_region = self.get_pixel_region(screen.shape, region)
            elif (
                isinstance(region, (tuple, list))
                and len(region) == 4
                and all(isinstance(v, (float, int)) and 0.0 <= v <= 1.0 for v in region)
                and any(isinstance(v, float) for v in region)
            ):
                pixel_region = self.get_pixel_region(screen.shape, region)
            else:
                pixel_region = region

            # Tier 1: Search targeted region
            match = self.find_text(
                labels,
                screen=screen,
                region=pixel_region,
                min_confidence=min_confidence,
                prefer_lower_half=False,
            )
            if match:
                self._record_locator(
                    locator=locator_name, tier="target_region_ocr", region=region_name,
                    duration_ms=(time.perf_counter() - started) * 1000.0, found=True,
                    confidence=match.get("confidence"),
                )
                return match

            # Tier 2: Search expanded region
            expanded = self.expand_region(screen.shape, pixel_region, ratio=expand_ratio)
            if expanded != pixel_region:
                match = self.find_text(
                    labels,
                    screen=screen,
                    region=expanded,
                    min_confidence=min_confidence,
                    prefer_lower_half=False,
                )
                if match:
                    self._record_locator(
                        locator=locator_name, tier="expanded_region_ocr", region=self._region_name(expanded),
                        duration_ms=(time.perf_counter() - started) * 1000.0, found=True,
                        confidence=match.get("confidence"), fallback_reason="target_region_miss",
                    )
                    return match

        # Tier 3: Measured full-screen fallback
        match = self.find_text(
            labels,
            screen=screen,
            region=None,
            min_confidence=min_confidence,
            prefer_lower_half=prefer_lower_half,
        )
        self._record_locator(
            locator=locator_name, tier="full_screen_ocr", region="full_screen",
            duration_ms=(time.perf_counter() - started) * 1000.0, found=bool(match),
            confidence=match.get("confidence") if match else None,
            fallback_reason="local_regions_exhausted" if region is not None else None,
        )
        return match

    @staticmethod
    def similarity(before: np.ndarray, after: np.ndarray) -> float:
        """Return structural screen similarity in the range 0..1."""
        if before is None or after is None or before.shape != after.shape:
            return 0.0
        before_gray = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
        after_gray = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY)
        if structural_similarity is not None:
            return float(structural_similarity(before_gray, after_gray, data_range=255))
        difference = cv2.absdiff(before_gray, after_gray)
        return float(1.0 - np.mean(difference) / 255.0)

    def _load_cache(self) -> dict:
        """Load element position cache for this profile."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self) -> None:
        """Persist element position cache atomically."""
        os.makedirs(self.cache_dir, exist_ok=True)
        tmp_file = f"{self.cache_file}.tmp"
        try:
            with open(tmp_file, "w") as f:
                json.dump(self._cache, f, indent=2)
            os.replace(tmp_file, self.cache_file)
        except Exception as e:
            print(f"Warning: Failed to save element cache: {e}")
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass

    def get_cached_hint(
        self,
        label: str,
        screen_shape: tuple[int, ...],
    ) -> tuple[int, int] | None:
        """
        Retrieve cached coordinate hint if valid (not expired by TTL and < MAX_CONSECUTIVE_FAILURES).
        Supports both new normalized metadata format and legacy [x, y] format.
        """
        cache_key = self._element_cache_key(label)
        if cache_key in self._cache:
            entry_key = cache_key
        elif label in self._cache:
            # Backwards-compatible lookup for caches created before context keys.
            entry_key = label
        else:
            return None

        entry = self._cache[entry_key]

        # Handle legacy format: [x, y]
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            return (int(entry[0]), int(entry[1]))

        if not isinstance(entry, dict):
            return None

        # Check consecutive failures
        if entry.get("failure_count", 0) >= self.MAX_CONSECUTIVE_FAILURES:
            del self._cache[entry_key]
            self._save_cache()
            return None

        # Check TTL
        last_verified = entry.get("last_verified_at")
        if last_verified:
            try:
                verified_dt = datetime.fromisoformat(last_verified)
                if verified_dt.tzinfo is None:
                    verified_dt = verified_dt.replace(tzinfo=timezone.utc)
                now_dt = datetime.now(timezone.utc)
                age_hours = (now_dt - verified_dt).total_seconds() / 3600.0
                if age_hours > self.CACHE_TTL_HOURS:
                    del self._cache[entry_key]
                    self._save_cache()
                    return None
            except Exception:
                pass

        norm = entry.get("normalized_center")
        if norm and len(norm) == 2:
            sh, sw = screen_shape[:2]
            cx = int(round(norm[0] * sw))
            cy = int(round(norm[1] * sh))
            return (cx, cy)

        return None

    def record_cache_hit(
        self,
        label: str,
        pos: tuple[int, int],
        screen_shape: tuple[int, ...],
    ) -> None:
        """Record a verified element localization into persistent cache."""
        sh, sw = screen_shape[:2]
        norm_x = round(pos[0] / max(1, sw), 4)
        norm_y = round(pos[1] / max(1, sh), 4)

        cache_key = self._element_cache_key(label)
        entry = self._cache.get(cache_key)
        success_count = 1
        if isinstance(entry, dict):
            success_count = entry.get("success_count", 0) + 1

        self._cache[cache_key] = {
            "normalized_center": [norm_x, norm_y],
            "screen_size": [sw, sh],
            "last_verified_at": datetime.now(timezone.utc).isoformat(),
            "success_count": success_count,
            "failure_count": 0,
        }
        self._save_cache()

    def record_cache_miss(self, label: str) -> None:
        """Record a verification miss; auto-invalidates after MAX_CONSECUTIVE_FAILURES."""
        cache_key = self._element_cache_key(label)
        if cache_key not in self._cache and label in self._cache:
            cache_key = label
        if cache_key not in self._cache:
            return
        entry = self._cache[cache_key]
        if isinstance(entry, dict):
            failures = entry.get("failure_count", 0) + 1
            entry["failure_count"] = failures
            if failures >= self.MAX_CONSECUTIVE_FAILURES:
                del self._cache[cache_key]
        else:
            del self._cache[cache_key]
        self._save_cache()

    def find_template(
        self,
        screen_img: np.ndarray,
        template_name: str,
        threshold: float = 0.80,
    ) -> tuple[int, int] | None:
        """
        Locate element using OpenCV normalized cross-correlation template matching.
        Returns center (cx, cy) if confidence >= threshold, otherwise None.
        """
        template_filename = (
            template_name if template_name.endswith(".png") else f"{template_name}.png"
        )
        template_path = os.path.join(TEMPLATE_DIR, template_filename)

        if not os.path.exists(template_path):
            return None

        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if template is None or screen_img is None:
            return None

        th, tw = template.shape[:2]
        sh, sw = screen_img.shape[:2]

        if th > sh or tw > sw:
            return None

        # 1:1 scale template match (fastest)
        res = cv2.matchTemplate(screen_img, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

        if max_val >= threshold:
            cx = max_loc[0] + tw // 2
            cy = max_loc[1] + th // 2
            return int(cx), int(cy)

        # Multi-scale matching fallback (handles DPI / zoom differences in uploaded crops)
        scales = (0.5, 0.65, 0.75, 0.85, 1.15, 1.25, 1.4)
        best_val = max_val
        best_pos = None

        for scale in scales:
            resized_w = int(tw * scale)
            resized_h = int(th * scale)
            if resized_h > sh or resized_w > sw or resized_w < 10 or resized_h < 10:
                continue
            resized = cv2.resize(template, (resized_w, resized_h), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
            res_scale = cv2.matchTemplate(screen_img, resized, cv2.TM_CCOEFF_NORMED)
            _, s_max_val, _, s_max_loc = cv2.minMaxLoc(res_scale)

            if s_max_val >= threshold and s_max_val > best_val:
                best_val = s_max_val
                best_pos = (s_max_loc[0] + resized_w // 2, s_max_loc[1] + resized_h // 2)

        return best_pos

    def find_element(
        self, label: str, threshold: float = 0.80, use_cache: bool = True
    ) -> tuple[int, int] | None:
        """
        Locate an element using the 3-tier hybrid approach:
        1. Check position cache (0ms)
        2. OpenCV template match (10-30ms)
        3. Cache verified coordinates
        """
        started = time.perf_counter()
        screen = self.client.screenshot()
        if screen is None or screen.size == 0:
            self._record_locator(
                locator=label, tier="screen_unavailable", region="full_screen",
                duration_ms=(time.perf_counter() - started) * 1000.0, found=False,
                fallback_reason="screen_unavailable",
            )
            return None

        # Tier 1: Check cache
        if use_cache:
            cached_pos = self.get_cached_hint(label, screen.shape)
            if cached_pos:
                # Quick crop validation: ensure element is still near cached location
                matched = self.find_template(screen, label, threshold=threshold)
                if matched:
                    dist = np.hypot(matched[0] - cached_pos[0], matched[1] - cached_pos[1])
                    if dist < 40:
                        self.record_cache_hit(label, cached_pos, screen.shape)
                        self._record_locator(
                            locator=label, tier="validated_cache_hint", region="cached_neighborhood",
                            duration_ms=(time.perf_counter() - started) * 1000.0, found=True,
                        )
                        return cached_pos
                    else:
                        self.record_cache_miss(label)
                else:
                    self.record_cache_miss(label)

        # Tier 2: Template match
        pos = self.find_template(screen, label, threshold=threshold)
        if pos:
            self.record_cache_hit(label, pos, screen.shape)
            self._record_locator(
                locator=label, tier="template_match", region="full_screen",
                duration_ms=(time.perf_counter() - started) * 1000.0, found=True,
                fallback_reason="cache_miss" if use_cache else None,
            )
            return pos

        self._record_locator(
            locator=label, tier="template_match", region="full_screen",
            duration_ms=(time.perf_counter() - started) * 1000.0, found=False,
            fallback_reason="all_local_tiers_missed",
        )
        return None

    def find_photo_video_button(
        self,
        threshold: float = 0.72,
        screen: np.ndarray | None = None,
        region: tuple[int, int, int, int] | str | None = None,
    ) -> tuple[int, int] | None:
        """
        Locate Facebook's 'Photo/video' button with multi-tier detection:
        1. Cache lookup
        2. Template match ('photo_video_btn' or 'photo_icon')
        3. Signature HSV color segmentation for Facebook's #45BD62 photo icon
        """
        screen = self.capture_screen() if screen is None else screen
        if screen is None or screen.size == 0:
            return None

        crop, offset_x, offset_y = self._crop_region(screen, region)
        if crop.size == 0:
            return None

        # Tier 1: Cache check (only for full-screen queries without custom region)
        if region is None:
            cached_pos = self.get_cached_hint("photo_video_btn", screen.shape)
            if cached_pos:
                matched = self.find_template(screen, "photo_icon", threshold=0.70)
                if matched and np.hypot(matched[0] - cached_pos[0], matched[1] - cached_pos[1]) < 60:
                    self.record_cache_hit("photo_video_btn", cached_pos, screen.shape)
                    return cached_pos
                else:
                    self.record_cache_miss("photo_video_btn")

        # Tier 2: Template match full button or icon
        for tpl in ("photo_video_btn", "photo_icon", "modal_photo_icon"):
            pos = self.find_template(crop, tpl, threshold=threshold)
            if pos:
                full_pos = (pos[0] + offset_x, pos[1] + offset_y)
                if region is None:
                    self.record_cache_hit("photo_video_btn", full_pos, screen.shape)
                return full_pos

        # Tier 3: HSV color segmentation for signature green icon (#45BD62)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        lower_green = np.array([65, 120, 120])
        upper_green = np.array([85, 255, 255])
        mask = cv2.inRange(hsv, lower_green, upper_green)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = cv2.contourArea(cnt)
            # Facebook photo camera icon is ~18x18 to 28x28
            if 80 < area < 1000 and 0.6 < (w / h) < 1.6:
                candidates.append((x + w // 2 + offset_x, y + h // 2 + offset_y, area))

        if candidates:
            # Sort by area/prominence
            candidates.sort(key=lambda c: c[2], reverse=True)
            best_pos = (candidates[0][0], candidates[0][1])
            if region is None:
                self.record_cache_hit("photo_video_btn", best_pos, screen.shape)
            return best_pos

        return None

    def find_comment_input(
        self,
        screen: np.ndarray | None = None,
    ) -> tuple[int, int] | None:
        """
        Locate the Facebook 'Comment as ...' or 'Write a comment...' input field.
        Scans native-resolution regions (preventing downscale blur on grey placeholder text).
        Returns clickable coordinates inside the input pill, or None.
        """
        screen = self.capture_screen() if screen is None else screen
        if screen is None or screen.size == 0:
            return None

        h, w = screen.shape[:2]

        candidate_regions = [
            # 1. Active modal bottom area (e.g. permalink overlay / post view)
            (max(0, int(w * 0.25)), int(h * 0.40), min(w, int(w * 0.55)), int(h * 0.60)),
            # 2. Main feed post stream column (e.g. profile page or newsfeed)
            (max(0, int(w * 0.28)), 0, min(w - int(w * 0.28), 1050), h),
            # 3. Full-screen fallback
            None,
        ]

        for region in candidate_regions:
            ocr_items = self.read_text(screen, region=region, min_confidence=0.15)
            matches = []
            for item in ocr_items:
                raw_text = item["text"].casefold().strip()
                normalized = re.sub(r"[^a-z0-9]+", " ", raw_text).strip()
                words = set(normalized.split())

                if "comment as" in raw_text or raw_text.startswith("comment as") or {"comment", "as"}.issubset(words):
                    matches.append((item, 10))
                elif any(phrase in raw_text for phrase in ("write a comment", "write a public comment")):
                    matches.append((item, 8))

            if matches:
                # A profile can expose several post comment pills at once. The
                # first post is the topmost matching pill, not the OCR match
                # with the highest confidence (which may belong to a later post).
                matches.sort(key=lambda m: (
                    m[0].get("center", (0, h + 1))[1],
                    -m[1],
                    -m[0].get("confidence", 0.0),
                ))
                best_item = matches[0][0]
                bx1, by1, bx2, by2 = best_item["bounds"]
                click_x = min(w - 20, bx1 + 60)
                click_y = (by1 + by2) // 2
                return (click_x, click_y)

        return None

    def find_blue_action_buttons(
        self,
        screen: np.ndarray | None = None,
        region: tuple[int, int, int, int] | tuple[float, float, float, float] | str | None = None,
    ) -> list[dict]:
        """
        Locate enabled Facebook-blue CTA rectangles.

        Returning the detected bounds lets callers OCR only pixels belonging to
        the button instead of using a fixed-width crop that can include feed text.
        """
        screen = self.capture_screen() if screen is None else screen
        if screen is None:
            return []
        crop, offset_x, offset_y = self._crop_region(screen, region)
        if crop.size == 0:
            return []
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        lower_blue = np.array([100, 150, 150])
        upper_blue = np.array([130, 255, 255])
        mask = cv2.inRange(hsv, lower_blue, upper_blue)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[dict] = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w > 80 and 20 < h < 60:
                x1 = x + offset_x
                y1 = y + offset_y
                candidates.append({
                    "center": (x1 + w // 2, y1 + h // 2),
                    "bounds": (x1, y1, x1 + w, y1 + h),
                    "area": int(w * h),
                })
        candidates.sort(key=lambda item: (item["center"][1], item["area"]), reverse=True)
        return candidates

    def find_blue_action_button(
        self,
        screen: np.ndarray | None = None,
        region: tuple[int, int, int, int] | tuple[float, float, float, float] | str | None = None,
    ) -> tuple[int, int] | None:
        """Return the center of the bottom-most enabled Facebook-blue CTA."""
        candidates = self.find_blue_action_buttons(screen=screen, region=region)
        return candidates[0]["center"] if candidates else None

    def find_stable(
        self,
        locator,
        attempts: int = 2,
        tolerance_px: float = 6.0,
        interval: float = 0.35,
    ) -> tuple[int, int] | None:
        """Require a locator to return nearly identical coordinates repeatedly."""
        previous = None
        stable_count = 0
        for _ in range(max(2, attempts + 1)):
            current = locator()
            if current is None:
                previous = None
                stable_count = 0
            elif previous is not None and np.hypot(current[0] - previous[0], current[1] - previous[1]) <= tolerance_px:
                stable_count += 1
                if stable_count >= attempts - 1:
                    return current
            else:
                previous = current
                stable_count = 0
            time.sleep(interval)
        return None

    def wait_for_element(
        self, label: str, timeout: float = 15.0, poll_interval: float = 0.8
    ) -> tuple[int, int] | None:
        """Poll virtual display until the target element is detected or timeout expires."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            pos = self.find_element(label)
            if pos:
                return pos
            time.sleep(poll_interval)
        return None

    def find_leave_site_button(
        self,
        screen: np.ndarray | None = None,
    ) -> tuple[int, int] | None:
        """
        Detect Chrome's native 'Leave site?' / 'Changes you made may not be saved'
        beforeunload modal prompt and return the center (x, y) coordinates of the 'Leave' button.
        """
        import re
        screen = self.capture_screen() if screen is None else screen
        if screen is None or screen.size == 0:
            return None

        for search_region in ("modal_header_alerts", None):
            items = self.read_text(screen, region=search_region, min_confidence=0.30)
            if not items:
                continue

            has_leave_prompt = False
            leave_candidate = None

            for item in items:
                norm_text = re.sub(r"[^a-z0-9 ]+", " ", item["text"].strip().lower()).strip()
                if (
                    "leave site" in norm_text
                    or "changes you made" in norm_text
                    or "may not be saved" in norm_text
                ):
                    has_leave_prompt = True
                if norm_text == "leave" or norm_text.endswith(" leave"):
                    leave_candidate = item

            # If both "Cancel" and "Leave" exist in the alert area, it is the beforeunload prompt
            has_cancel = any("cancel" in re.sub(r"[^a-z0-9 ]+", " ", item["text"].strip().lower()).split() for item in items)
            if has_cancel and leave_candidate:
                has_leave_prompt = True

            if has_leave_prompt and leave_candidate:
                return leave_candidate["center"]

            # If prompt was recognized, check for blue action button within that region
            if has_leave_prompt:
                blue = self.find_blue_action_button(screen=screen, region=search_region)
                if blue:
                    return blue
                if leave_candidate:
                    return leave_candidate["center"]

            if has_leave_prompt:
                break

        return None
