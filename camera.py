"""Webcam access with Windows backend fallback and helpful error messages."""
from __future__ import annotations

import logging
import os
import time

import cv2

log = logging.getLogger("assistant.camera")


class CameraError(Exception):
    """Raised with a user-readable message when the webcam cannot be used."""


class Camera:
    def __init__(self, index: int = 0, width: int = 640, height: int = 480):
        self.index, self.width, self.height = index, width, height
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> None:
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY] if os.name == "nt" else [cv2.CAP_ANY]
        opened_any = False
        got_black = False
        for backend in backends:
            cap = None
            try:
                cap = cv2.VideoCapture(self.index, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                opened_any = True
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                # Cameras need a moment for auto-exposure; discard warm-up frames.
                good = False
                for _ in range(40):
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        if frame.mean() > 2.0:
                            good = True
                            break
                        got_black = True
                    time.sleep(0.05)
                if good:
                    self._cap = cap
                    log.info("Camera %s opened with backend %s", self.index, backend)
                    return
                cap.release()
            except cv2.error as exc:
                log.warning("Backend %s failed: %s", backend, exc)
                if cap is not None:
                    cap.release()
        if got_black:
            raise CameraError(
                "The camera opened but only returns black frames. Check the Lenovo webcam "
                "privacy shutter / Fn key, and that the lens is not covered."
            )
        if opened_any:
            raise CameraError(
                "The camera opened but gives no pictures. Another app may be using it, or "
                "Windows camera privacy is blocking desktop apps "
                "(Settings > Privacy & security > Camera > 'Let desktop apps access your camera')."
            )
        raise CameraError(
            f"No camera found at index {self.index}. Check that the webcam is enabled, close any "
            "app that is using it (Teams, Zoom, Camera app), or change 'camera_index' in Settings."
        )

    def read(self):
        if self._cap is None:
            return None
        try:
            ok, frame = self._cap.read()
        except cv2.error:
            return None
        return frame if ok else None

    def release(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except cv2.error:
                pass
            self._cap = None
            log.info("Camera released")
