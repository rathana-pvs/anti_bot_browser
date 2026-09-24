"""Vision Engine for Element Localization.

Implements the 3-Tier Hybrid Locator:
  Tier 1: Position Cache (0ms)
  Tier 2: OpenCV Multi-Scale Template Matching (10–30ms)
  Tier 3: Intelligent Fallback & Coordinate Learning
"""

import json
import os
import time
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


class VisionEngine:
    _ocr_readers: dict[tuple[str, ...], object] = {}

    def __init__(self, client: ContainerClient):
        self.client = client
        self.cache_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "profiles", client.profile_id)
        )
        self.cache_file = os.path.join(self.cache_dir, "element_cache.json")
        self._cache = self._load_cache()

    def capture_screen(self) -> np.ndarray:
        """Capture the current screen through the configured container client."""
        return self.client.screenshot()

    @classmethod
    def _get_ocr_reader(cls, languages: tuple[str, ...] = ("en",)):
        """Lazily initialize one pretrained EasyOCR reader per language set."""
        if languages in cls._ocr_readers:
            return cls._ocr_readers[languages]
        try:
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
                download_enabled=True,
            )
        except Exception as exc:
            print(f"Warning: EasyOCR is unavailable: {exc}")
            reader = None
        cls._ocr_readers[languages] = reader
        return reader

    @staticmethod
    def _crop_region(screen: np.ndarray, region: tuple[int, int, int, int] | None):
        if region is None:
            return screen, 0, 0
        x, y, width, height = region
        sh, sw = screen.shape[:2]
        x = max(0, min(sw, x))
        y = max(0, min(sh, y))
        width = max(0, min(sw - x, width))
        height = max(0, min(sh - y, height))
        return screen[y : y + height, x : x + width], x, y

    def read_text(
        self,
        screen: np.ndarray | None = None,
        region: tuple[int, int, int, int] | None = None,
        min_confidence: float = 0.35,
        languages: tuple[str, ...] = ("en",),
    ) -> list[dict]:
        """Return OCR text, confidence, bounds, and center in screen coordinates."""
        screen = self.capture_screen() if screen is None else screen
        crop, offset_x, offset_y = self._crop_region(screen, region)
        if crop.size == 0:
            return []
        # UI text does not require native 1080p resolution. Capping the OCR input
        # substantially reduces CPU inference time while preserving readable text.
        scale = min(1.0, 1280.0 / max(crop.shape[:2]))
        ocr_image = crop
        if scale < 1.0:
            ocr_image = cv2.resize(
                crop,
                (int(crop.shape[1] * scale), int(crop.shape[0] * scale)),
                interpolation=cv2.INTER_AREA,
            )
        reader = self._get_ocr_reader(languages)
        if reader is None:
            return []

        results = []
        try:
            raw_results = reader.readtext(ocr_image, detail=1, paragraph=False)
        except Exception as exc:
            print(f"Warning: OCR failed: {exc}")
            return []

        for box, text, confidence in raw_results:
            confidence = float(confidence)
            if confidence < min_confidence:
                continue
            xs = [int(point[0] / scale) + offset_x for point in box]
            ys = [int(point[1] / scale) + offset_y for point in box]
            bounds = (min(xs), min(ys), max(xs), max(ys))
            results.append(
                {
                    "text": str(text).strip(),
                    "confidence": confidence,
                    "bounds": bounds,
                    "center": ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2),
                }
            )
        return results

    def find_text(
        self,
        labels: str | tuple[str, ...] | list[str],
        screen: np.ndarray | None = None,
        region: tuple[int, int, int, int] | None = None,
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
        """Persist element position cache."""
        os.makedirs(self.cache_dir, exist_ok=True)
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self._cache, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to save element cache: {e}")

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
        screen = self.client.screenshot()

        # Tier 1: Check cache
        if use_cache and label in self._cache:
            cached_pos = self._cache[label]
            # Quick crop validation: ensure element is still near cached location
            matched = self.find_template(screen, label, threshold=threshold)
            if matched:
                # If template matches within 30px of cached position, use it
                dist = np.hypot(matched[0] - cached_pos[0], matched[1] - cached_pos[1])
                if dist < 40:
                    return cached_pos

        # Tier 2: Template match
        pos = self.find_template(screen, label, threshold=threshold)
        if pos:
            self._cache[label] = list(pos)
            self._save_cache()
            return pos

        return None

    def find_photo_video_button(
        self,
        threshold: float = 0.72,
        screen: np.ndarray | None = None,
    ) -> tuple[int, int] | None:
        """
        Locate Facebook's 'Photo/video' button with multi-tier detection:
        1. Cache lookup
        2. Template match ('photo_video_btn' or 'photo_icon')
        3. Signature HSV color segmentation for Facebook's #45BD62 photo icon
        """
        screen = self.capture_screen() if screen is None else screen

        # Tier 1: Cache check
        if "photo_video_btn" in self._cache:
            cached_pos = self._cache["photo_video_btn"]
            matched = self.find_template(screen, "photo_icon", threshold=0.70)
            if matched and np.hypot(matched[0] - cached_pos[0], matched[1] - cached_pos[1]) < 60:
                return cached_pos

        # Tier 2: Template match full button or icon
        for tpl in ("photo_video_btn", "photo_icon", "modal_photo_icon"):
            pos = self.find_template(screen, tpl, threshold=threshold)
            if pos:
                self._cache["photo_video_btn"] = list(pos)
                self._save_cache()
                return pos

        # Tier 3: HSV color segmentation for signature green icon (#45BD62)
        hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
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
                candidates.append((x + w // 2, y + h // 2, area))

        if candidates:
            # Sort by area/prominence
            candidates.sort(key=lambda c: c[2], reverse=True)
            best_pos = (candidates[0][0], candidates[0][1])
            self._cache["photo_video_btn"] = list(best_pos)
            self._save_cache()
            return best_pos

        return None

    def find_blue_action_button(
        self,
        screen: np.ndarray | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int] | None:
        """
        Locate Facebook's primary blue CTA button in modals (e.g. 'Next' or 'Post').
        Uses signature Facebook primary blue (#0866FF) HSV color segmentation.
        """
        screen = self.capture_screen() if screen is None else screen
        if screen is None:
            return None
        crop, offset_x, offset_y = self._crop_region(screen, region)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        lower_blue = np.array([100, 150, 150])
        upper_blue = np.array([130, 255, 255])
        mask = cv2.inRange(hsv, lower_blue, upper_blue)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w > 80 and 20 < h < 60:
                candidates.append((x + w // 2 + offset_x, y + h // 2 + offset_y, y + offset_y))
        if candidates:
            # Sort by Y position descending to get bottom-most modal CTA
            candidates.sort(key=lambda item: item[2], reverse=True)
            return (candidates[0][0], candidates[0][1])
        return None

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
