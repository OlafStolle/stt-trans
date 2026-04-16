# app/daemon.py
"""evdev-Daemon: überwacht Tasten global und startet die Transkriptions-Pipeline."""
import asyncio
import logging
import evdev
from app.config import BlitztextConfig, load_config
from app.recorder import RecordingSession
from app.transcribe import transcribe_audio
from app.process import process_text, ProcessMode
from app.inject import inject_text, notify

logger = logging.getLogger("blitztext.daemon")

class BlitztextDaemon:
    def __init__(self, cfg: BlitztextConfig | None = None):
        self.cfg = cfg or load_config()
        self._active_mode: str | None = None
        self._session: RecordingSession | None = None
        self._running = False
        self._toggle_recording = False  # für trigger_mode == "toggle"

    def _key_to_mode(self, key_code: int) -> str | None:
        for mode_name, mode_cfg in self.cfg.modes.items():
            if mode_cfg.key_code == key_code:
                return mode_name
        return None

    async def _run_pipeline(self, mode_name: str, wav_bytes: bytes) -> None:
        """Whisper → LLM → inject."""
        try:
            notify("recording", f"Transkribiere ({mode_name})...")
            text = await transcribe_audio(
                wav_bytes,
                language=self.cfg.whisper_language,
                vocabulary=self.cfg.vocabulary,
            )
            if not text:
                notify("error", "Kein Text erkannt")
                return

            mode_cfg = self.cfg.modes[mode_name]
            text = await process_text(
                text,
                ProcessMode(mode_name),
                prompt=mode_cfg.prompt,
                emoji_count=mode_cfg.emoji_count,
            )
            inject_text(text, method=self.cfg.inject.method,
                        delay_ms=self.cfg.inject.delay_ms)
            notify("done", f"Eingefügt: {text[:40]}...")
        except Exception as e:
            logger.error("Pipeline error: %s", e)
            notify("error", f"Fehler: {e}")

    async def run(self) -> None:
        """Hauptschleife: öffnet evdev-Device und wartet auf Tasten."""
        self._running = True
        device_path = self.cfg.input_device
        if not device_path:
            logger.warning("Kein input_device konfiguriert — Daemon inaktiv.")
            return

        try:
            dev = evdev.InputDevice(device_path)
            logger.info("Blitztext Daemon gestartet auf %s", device_path)
            notify("done", "Blitztext bereit")
        except Exception as e:
            logger.error("Kann Device nicht öffnen: %s", e)
            return

        try:
            async for event in dev.async_read_loop():
                if not self._running:
                    break
                if event.type != evdev.ecodes.EV_KEY:
                    continue

                mode_name = self._key_to_mode(event.code)
                if mode_name is None:
                    continue

                audio_device = self.cfg.audio_device if self.cfg.audio_device != "default" else None

                if self.cfg.trigger_mode == "hold":
                    if event.value == 1:
                        self._active_mode = mode_name
                        self._session = RecordingSession(device=audio_device)
                        self._session.start()
                        notify("recording", f"Aufnahme ({mode_name})...")
                        logger.info("Recording started (hold): %s", mode_name)
                    elif event.value == 0:
                        if self._session and self._active_mode:
                            wav = self._session.stop()
                            mode = self._active_mode
                            self._session = None
                            self._active_mode = None
                            asyncio.create_task(self._run_pipeline(mode, wav))

                elif self.cfg.trigger_mode == "toggle":
                    if event.value == 1:
                        if not self._toggle_recording:
                            self._toggle_recording = True
                            self._active_mode = mode_name
                            self._session = RecordingSession(device=audio_device)
                            self._session.start()
                            notify("recording", f"Aufnahme ({mode_name})...")
                            logger.info("Recording started (toggle): %s", mode_name)
                        else:
                            self._toggle_recording = False
                            if self._session and self._active_mode:
                                wav = self._session.stop()
                                mode = self._active_mode
                                self._session = None
                                self._active_mode = None
                                asyncio.create_task(self._run_pipeline(mode, wav))

        except Exception as e:
            logger.error("Daemon loop error: %s", e)
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
        if self._session:
            self._session.stop()
