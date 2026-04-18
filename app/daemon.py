# app/daemon.py
"""evdev-Daemon: überwacht Tasten global und startet die Transkriptions-Pipeline."""
import asyncio
import logging
import subprocess
import evdev
from app.config import BlitztextConfig, load_config
from app.recorder import RecordingSession, LiveRecordingSession, find_monitor_device
from app.transcribe import transcribe_audio
from app.process import process_text, ProcessMode
from app.inject import inject_text, notify, _copy_to_clipboard
from app.routes import live

logger = logging.getLogger("stt-trans.daemon")

class BlitztextDaemon:
    def __init__(self, cfg: BlitztextConfig | None = None):
        self.cfg = cfg or load_config()
        self._active_mode: str | None = None
        self._session: RecordingSession | None = None
        self._running = False
        self._toggle_recording = False  # für trigger_mode == "toggle"
        self._pressed_keys: set[int] = set()
        self._live_mic_session: LiveRecordingSession | None = None
        self._live_desktop_session: LiveRecordingSession | None = None
        import time as _t
        self._key_down_time: float = _t.monotonic()  # initialized to now to avoid bogus held times
        self._wait_for_next_up: bool = False  # Toggle-Verhalten bei kurzen Taps
        self._startup_flush: bool = True  # ignore stale events from kernel buffer at startup

    def _key_to_mode(self, key_code: int) -> str | None:
        for mode_name, mode_cfg in self.cfg.modes.items():
            if mode_cfg.key_code == key_code:
                return mode_name
        return None

    def _key_to_mode_combo(self) -> str | None:
        best_mode: str | None = None
        best_len: int = 0
        best_has_key_name: bool = False
        for mode_name, mode_cfg in self.cfg.modes.items():
            combo = set(mode_cfg.effective_key_codes)
            if combo and combo <= self._pressed_keys:
                has_key_name = bool(mode_cfg.key_name)
                if len(combo) > best_len:
                    best_len = len(combo)
                    best_mode = mode_name
                    best_has_key_name = has_key_name
                elif len(combo) == best_len and has_key_name and not best_has_key_name:
                    # Gleichstand: bevorzuge Modus mit gesetztem key_name
                    best_mode = mode_name
                    best_has_key_name = True
        return best_mode

    async def _toggle_live(self) -> None:
        """Startet oder stoppt den Live-Transkriptions-Modus."""
        if self._session is not None:
            logger.warning("PTT läuft — Live-Modus nicht gestartet")
            return

        # Stop-Button im Browser könnte live._live_mic_session geleert haben
        if self._live_mic_session is not None and live._live_mic_session is None:
            self._live_mic_session = None
            self._live_desktop_session = None

        if self._live_mic_session is None:
            loop = asyncio.get_running_loop()
            monitor = find_monitor_device()
            audio_device = self.cfg.audio_device if self.cfg.audio_device != "default" else "pulse"
            mic = LiveRecordingSession(device=audio_device)
            desktop = LiveRecordingSession(device=monitor) if monitor else None
            mic.start(loop)
            if desktop:
                desktop.start(loop)
            self._live_mic_session = mic
            self._live_desktop_session = desktop
            live.set_sessions(mic, desktop)
            live.set_stop_callback(self._on_live_stopped)
            asyncio.create_task(live.start_pumps())
            notify("recording", "Live-Transkription gestartet")
            try:
                subprocess.Popen(
                    ["xdg-open", "http://localhost:8765/live"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                logger.warning("xdg-open nicht gefunden — öffne http://localhost:8765/live manuell")
        else:
            self._live_mic_session.stop()
            self._live_mic_session = None
            if self._live_desktop_session:
                self._live_desktop_session.stop()
                self._live_desktop_session = None
            live.set_sessions(None, None)
            live.set_stop_callback(None)
            notify("done", "Live-Transkription beendet")

    async def _run_pipeline(self, mode_name: str, wav_bytes: bytes) -> None:
        """Whisper → LLM → inject."""
        try:
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
            # Clipboard nur setzen wenn die inject-Methode das nicht selbst tut,
            # sonst blockieren sich die beiden wl-copy/xclip Daemons gegenseitig.
            if self.cfg.inject.method not in ("wl-copy+paste", "xclip+paste"):
                _copy_to_clipboard(text)
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
            logger.info("stt-trans Daemon gestartet auf %s", device_path)
            notify("done", "stt-trans bereit")
        except Exception as e:
            logger.error("Kann Device nicht öffnen: %s", e)
            return

        try:
            async for event in dev.async_read_loop():
                if not self._running:
                    break
                if event.type != evdev.ecodes.EV_KEY:
                    continue

                # Pressed-Keys-Set aktualisieren
                import time as _time

                # Discard stale UP/REPEAT events buffered before daemon started
                if self._startup_flush:
                    if event.value == 1:
                        self._startup_flush = False  # first real DOWN → flush done
                    else:
                        continue  # skip UP/REPEAT until first DOWN seen

                _val_str = {0: "UP", 1: "DOWN", 2: "REPEAT"}.get(event.value, str(event.value))
                logger.debug("KEY %s code=%d pressed=%s", _val_str, event.code, self._pressed_keys)
                if event.value == 1:
                    self._pressed_keys.add(event.code)
                    self._key_down_time = _time.monotonic()
                elif event.value == 0:
                    _held = _time.monotonic() - self._key_down_time
                    logger.info("KEY_UP code=%d held=%.3fs", event.code, _held)
                    self._pressed_keys.discard(event.code)

                audio_device = self.cfg.audio_device if self.cfg.audio_device != "default" else "pulse"

                if event.value == 1:
                    # Live-Key prüfen
                    live_combo = set(self.cfg.live_key_codes)
                    if live_combo and live_combo <= self._pressed_keys:
                        asyncio.create_task(self._toggle_live())
                        continue

                    # Aufbauende Live-Combo: PTT unterdrücken solange gedrückte Keys
                    # noch eine Teilmenge der Live-Combo sein könnten
                    if live_combo and self._pressed_keys <= live_combo:
                        continue

                    # PTT während Live ignorieren
                    if self._live_mic_session is not None:
                        continue

                    # Key gedrückt — prüfe ob Combo komplett
                    mode_name = self._key_to_mode_combo()
                    if mode_name is None:
                        continue
                    trigger = self.cfg.trigger_mode

                    if trigger == "hold" and self._active_mode is None:
                        try:
                            self._session = RecordingSession(device=audio_device)
                            self._session.start()
                            self._active_mode = mode_name
                            notify("recording", f"Aufnahme ({mode_name})...")
                            logger.info("Recording started (hold): %s", mode_name)
                        except Exception as rec_err:
                            logger.error("Recording start failed: %s", rec_err)
                            notify("error", f"Mikrofon nicht gefunden: {audio_device}")
                            self._session = None
                            self._active_mode = None

                    elif trigger == "toggle":
                        if not self._toggle_recording:
                            try:
                                self._session = RecordingSession(device=audio_device)
                                self._session.start()
                                self._toggle_recording = True
                                self._active_mode = mode_name
                                notify("recording", f"Aufnahme ({mode_name})...")
                            except Exception as rec_err:
                                logger.error("Recording start failed: %s", rec_err)
                                notify("error", f"Mikrofon nicht gefunden: {audio_device}")
                                self._session = None
                                self._active_mode = None
                            logger.info("Recording started (toggle): %s", mode_name)
                        else:
                            self._toggle_recording = False
                            if self._session and self._active_mode:
                                wav = self._session.stop()
                                mode = self._active_mode
                                self._session = None
                                self._active_mode = None
                                asyncio.create_task(self._run_pipeline(mode, wav))

                elif event.value == 0:
                    # Key losgelassen — bei hold: Recording stoppen wenn aktiv
                    if self._session and self._active_mode:
                        mode_cfg = self.cfg.modes.get(self._active_mode)
                        if mode_cfg and self.cfg.trigger_mode == "hold":
                            combo = set(mode_cfg.effective_key_codes)
                            if event.code in combo:  # Einer der Combo-Keys losgelassen
                                held_s = _time.monotonic() - self._key_down_time
                                if held_s < 0.4 and not self._wait_for_next_up:
                                    # Kurzer Tap (<400ms): in Toggle-Modus wechseln
                                    self._wait_for_next_up = True
                                    logger.info("Kurzer Tap (%.3fs) — warte auf nächsten UP zum Stoppen", held_s)
                                else:
                                    # Langer Hold oder zweiter UP → Aufnahme stoppen
                                    self._wait_for_next_up = False
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

    def _on_live_stopped(self) -> None:
        """Browser hat Live gestoppt — Daemon-State synchronisieren."""
        self._live_mic_session = None
        self._live_desktop_session = None

    def stop(self) -> None:
        self._running = False
        if self._session:
            self._session.stop()
        if self._live_mic_session:
            self._live_mic_session.stop()
            self._live_mic_session = None
        if self._live_desktop_session:
            self._live_desktop_session.stop()
            self._live_desktop_session = None
