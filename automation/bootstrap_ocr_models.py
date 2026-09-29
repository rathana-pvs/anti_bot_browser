"""Install the EasyOCR weights used by the visual automation engine."""

from pathlib import Path

import easyocr


def main() -> None:
    model_dir = Path(__file__).resolve().parent / "models" / "easyocr"
    model_dir.mkdir(parents=True, exist_ok=True)
    easyocr.Reader(
        ["en"],
        gpu=False,
        verbose=False,
        model_storage_directory=str(model_dir),
        download_enabled=True,
    )

    required = ("craft_mlt_25k.pth", "english_g2.pth")
    missing = [name for name in required if not (model_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"EasyOCR model installation is incomplete: {', '.join(missing)}")
    print("EasyOCR models are ready.")


if __name__ == "__main__":
    main()
