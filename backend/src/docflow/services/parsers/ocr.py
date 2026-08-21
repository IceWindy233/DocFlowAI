from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


class OcrEngine:
    def recognize(self, image_path: Path) -> tuple[str, str]:
        if importlib.util.find_spec("rapidocr_onnxruntime") is not None:
            try:
                from rapidocr_onnxruntime import RapidOCR

                result, _ = RapidOCR()(str(image_path))
                if result:
                    return "\n".join(str(line[1]) for line in result), "rapidocr"
            except Exception:
                pass
        try:
            command = ["tesseract", str(image_path), "stdout", "-l", "chi_sim+eng", "--psm", "6"]
            process = subprocess.run(
                command, check=True, capture_output=True, text=True, timeout=180
            )
            return process.stdout.strip(), "tesseract"
        except (OSError, subprocess.SubprocessError):
            return "", "unavailable"
