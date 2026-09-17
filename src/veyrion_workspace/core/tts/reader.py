"""Read-aloud subsystem (TTS).

Runs a pyttsx3 engine loop on a dedicated thread with a sentence queue so
the GUI never blocks. Emits sentence-boundary callbacks so the UI can
highlight the current sentence.
"""
from __future__ import annotations

import logging
import queue
import re
import threading

logger = logging.getLogger("veyrion.tts")

try:
    import pyttsx3
    HAS_TTS = True
except ImportError:  # pragma: no cover
    HAS_TTS = False

_SENTENCE_RE = re.compile(r"[^.!?…]+[.!?…]+[\"')\]]*|[^.!?…]+$")


def split_sentences(text: str) -> list[str]:
    """Split text into speakable sentences (kept modest in length)."""
    sentences = []
    for m in _SENTENCE_RE.finditer(text.replace("\n", " ")):
        s = m.group(0).strip()
        if s:
            sentences.append(s)
    # Merge tiny fragments into neighbors.
    merged: list[str] = []
    for s in sentences:
        if merged and len(s) < 12:
            merged[-1] = merged[-1] + " " + s
        elif merged and len(merged[-1]) < 12:
            merged[-1] = merged[-1] + " " + s
        else:
            merged.append(s)
    return merged


class TtsReader:
    """Background speech with play/pause/stop/next/previous."""

    def __init__(self) -> None:
        self._engine = None
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._playing = threading.Event()
        self._stopped = threading.Event()
        self._current_index = -1
        self._sentences: list[str] = []
        self.rate = 1.0
        self.volume = 1.0
        self.voice_id = ""
        self.on_sentence: callable | None = None  # fn(index, sentence)
        self.on_end: callable | None = None
        self._tokens: list = []

    @property
    def available(self) -> bool:
        return HAS_TTS

    def _ensure_engine(self):
        if self._engine is None and HAS_TTS:
            self._engine = pyttsx3.init()
            self._apply_settings()
        return self._engine

    def _apply_settings(self) -> None:
        if self._engine is None:
            return
        try:
            self._engine.setProperty("rate", int(200 * self.rate))
            self._engine.setProperty("volume", max(0.0, min(1.0, self.volume)))
            if self.voice_id:
                for voice in self._engine.getProperty("voices"):
                    if voice.id == self.voice_id:
                        self._engine.setProperty("voice", voice.id)
                        break
        except Exception:
            logger.debug("tts settings failed", exc_info=True)

    def voices(self) -> list[dict]:
        if not HAS_TTS:
            return []
        try:
            engine = self._ensure_engine()
            return [{"id": v.id, "name": v.name}
                    for v in engine.getProperty("voices")]
        except Exception:
            return []

    # -- queue control -------------------------------------------------------
    def speak(self, text: str, start: int = 0) -> None:
        """Queue ``text`` and begin reading at sentence ``start``."""
        self._sentences = split_sentences(text)
        self._queue = queue.Queue()
        for idx, s in enumerate(self._sentences):
            self._queue.put((idx, s))
        self._current_index = start - 1 if start > 0 else -1
        if start > 0:
            for _ in range(start):
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        self.start()

    def start(self) -> None:
        if not HAS_TTS:
            return
        self._stopped.clear()
        self._playing.set()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="or-tts",
                                            daemon=True)
            self._thread.start()

    def pause(self) -> None:
        self._playing.clear()

    def resume(self) -> None:
        self._playing.set()

    def stop(self) -> None:
        self._stopped.set()
        self._playing.set()  # unblock waits
        with self._lock:
            try:
                if self._engine is not None:
                    self._engine.stop()
            except Exception:
                logger.exception("stop failed")
        self._queue = queue.Queue()

    def next_sentence(self) -> None:
        try:
            self._queue.put((self._skip_token(), ""))
        except Exception:
            logger.exception("next_sentence failed")

    def _skip_token(self):
        return ("__skip__",)

    def previous_sentence(self) -> None:
        idx = max(0, self._current_index - 1)
        self._queue.queue.clear()
        self._queue.put(("__seek__", idx))

    def seek(self, index: int) -> None:
        self._queue.queue.clear()
        self._queue.put(("__seek__", max(0, index)))

    # -- worker -----------------------------------------------------------
    def _loop(self) -> None:
        engine = self._ensure_engine()
        if engine is None:
            return
        # Run the engine loop entirely from this thread.
        while True:
            if self._stopped.is_set():
                break
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item[0] == "__skip__":
                continue
            if item[0] == "__seek__":
                idx = item[1]
                # rebuild queue from sentences[idx:]
                self._queue.queue.clear()
                for j in range(idx, len(self._sentences)):
                    self._queue.put((j, self._sentences[j]))
                self._current_index = idx - 1
                continue
            idx, sentence = item
            if idx <= self._current_index:
                continue
            self._current_index = idx
            if self.on_sentence:
                try:
                    self.on_sentence(idx, sentence)
                except Exception:
                    logger.exception("_loop failed")
            # Wait until playing.
            while self._playing.is_set() is False and not self._stopped.is_set():
                threading.Event().wait(0.1)
            if self._stopped.is_set():
                break
            self._apply_settings()
            try:
                engine.say(sentence)
                engine.runAndWait()
            except Exception:
                logger.exception("tts say failed")
                break
        if self.on_end:
            try:
                self.on_end()
            except Exception:
                logger.exception("_loop failed")

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def sentence_count(self) -> int:
        return len(self._sentences)

    def shutdown(self) -> None:
        self.stop()
        try:
            if self._engine is not None:
                self._engine.stop()
        except Exception:
            logger.exception("shutdown failed")
