"""Recording backend.

Deliberately simple: this launches ``arecord | flac`` as two ordinary
subprocesses wired together by a pipe, and stops them from Python — no shell
wrapper, no systemd-run, no signal gymnastics. Stopping sends SIGTERM to
``arecord`` (which finalises and closes its output); ``flac`` then sees EOF,
finishes the file, and we atomically move it into place.

Audio is staged to a hidden ``.incoming/`` file and moved into ``Music/Mixes``
only when complete, so Syncthing never sees a partial file.

On a dev machine (no ``arecord``/``flac``, or ``enabled = false``) the recorder
reports itself unavailable and the UI degrades gracefully — the landing page
still works.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path


class RecorderError(RuntimeError):
    pass


# folders excluded from the "recent recordings" list
_SKIP_DIRS = {".incoming", ".stversions", "_dupes", "_review", "_assets",
              "Downloaded", "Edits"}
# audio formats shown in "recent" / downloadable / playable
AUDIO_EXTS = {".flac", ".wav", ".mp3", ".m4a", ".aif", ".aiff", ".ogg"}


class Recorder:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True))
        self.device = cfg.get("device", "hw:CODEC")
        self.rate = int(cfg.get("sample_rate", 44100))
        self.channels = int(cfg.get("channels", 2))
        self.mixes_dir = Path(cfg.get("mixes_dir", "~/Music/Mixes")).expanduser()
        self.state_dir = Path(cfg.get("state_dir", "/run/swarbs-home")).expanduser()
        self.recent_count = int(cfg.get("recent_count", 10))
        # "year" -> file recordings into a <YYYY>/ subfolder; "none" -> root
        self.organize = cfg.get("organize", "year")

        self._lock = threading.Lock()
        self._arec: subprocess.Popen | None = None
        self._flac: subprocess.Popen | None = None
        self._err = None

    # ---- availability -------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self.enabled and shutil.which("arecord") and shutil.which("flac"))

    def unavailable_reason(self) -> str:
        if not self.enabled:
            return "recorder disabled in config"
        missing = [t for t in ("arecord", "flac") if not shutil.which(t)]
        if missing:
            return f"missing tools: {', '.join(missing)} (dev machine?)"
        return ""

    # ---- state file (for status display + restart fallback) ----------
    def _state_file(self) -> Path:
        return self.state_dir / "current.json"

    def _read_state(self) -> dict:
        try:
            return json.loads(self._state_file().read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def _write_state(self, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file().write_text(json.dumps(data))

    def _clear_state(self) -> None:
        self._state_file().unlink(missing_ok=True)

    @property
    def _err_path(self) -> Path:
        return self.state_dir / "arecord.err"

    # ---- process helpers ---------------------------------------------
    @staticmethod
    def _alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _running(self) -> bool:
        if self._flac is not None:
            return self._flac.poll() is None
        # after a web-app restart we have no handles; fall back to the state file
        st = self._read_state()
        return self._alive(st.get("flac_pid")) or self._alive(st.get("arec_pid"))

    # ---- public API ---------------------------------------------------
    def status(self) -> dict:
        base = {
            "available": self.available,
            "recording": False,
            "failed": False,
            "elapsed": 0,
            "filename": None,
            "device": self.device,
            "next_filename": self._make_name(None),
            "disk_free_gb": self._disk_free_gb(),
            "recent": self._recent(),
            "reason": self.unavailable_reason(),
            "mixes_dir": str(self.mixes_dir),
        }
        if not self.available:
            return base

        if self._running():
            st = self._read_state()
            base["recording"] = True
            base["filename"] = st.get("file")
            if st.get("started_at"):
                base["elapsed"] = max(0, int(time.time() - st["started_at"]))
        else:
            # nothing running but state lingered -> clean up
            if self._read_state():
                self._clear_state()
        return base

    def start(self, label: str | None = None) -> str:
        with self._lock:
            if not self.available:
                raise RecorderError(self.unavailable_reason() or "recorder unavailable")
            if self._running():
                raise RecorderError("already recording")

            name = self._make_name(label)
            subdir = name[:4] if self.organize == "year" else ""   # YYYY from the name
            target_dir = self.mixes_dir / subdir if subdir else self.mixes_dir
            incoming = self.mixes_dir / ".incoming"
            try:
                incoming.mkdir(parents=True, exist_ok=True)
                target_dir.mkdir(parents=True, exist_ok=True)
                self.state_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RecorderError(f"cannot create {exc.filename or self.mixes_dir}: {exc}") from exc

            tmp = str(incoming / f"{name}.part")
            final = str(target_dir / name)

            arecord_cmd = [
                "arecord", "-D", self.device,
                "-f", "S16_LE", "-c", str(self.channels), "-r", str(self.rate),
                "-t", "wav", "-",
            ]
            flac_cmd = ["flac", "--totally-silent", "--best", "-o", tmp, "-"]

            err = open(self._err_path, "wb")
            try:
                arec = subprocess.Popen(
                    arecord_cmd, stdout=subprocess.PIPE, stderr=err,
                    start_new_session=True,
                )
            except OSError as exc:
                err.close()
                raise RecorderError(f"cannot launch arecord: {exc}") from exc
            try:
                flac = subprocess.Popen(
                    flac_cmd, stdin=arec.stdout, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as exc:
                arec.kill()
                err.close()
                raise RecorderError(f"cannot launch flac: {exc}") from exc
            arec.stdout.close()  # flac now owns the read end

            # If arecord dies straight away (device busy/unplugged/bad rate),
            # surface its error instead of pretending we're recording.
            time.sleep(0.4)
            if arec.poll() is not None:
                try:
                    flac.kill()
                except OSError:
                    pass
                err.flush()
                err.close()
                msg = self._read_err()
                raise RecorderError(msg or "arecord exited immediately (device busy or unplugged?)")

            self._arec, self._flac, self._err = arec, flac, err
            self._write_state({
                "file": name, "tmp": tmp, "final": final,
                "arec_pid": arec.pid, "flac_pid": flac.pid,
                "label": label, "started_at": time.time(),
            })
            return name

    def stop(self) -> None:
        with self._lock:
            st = self._read_state()
            arec_pid = st.get("arec_pid")
            flac_pid = st.get("flac_pid")

            # 1. stop arecord -> flac gets EOF and finalises the FLAC
            self._terminate(self._arec, arec_pid)
            # 2. wait for flac to finish writing
            self._wait(self._flac, flac_pid, timeout=15)
            # 3. make sure both are really gone
            self._terminate(self._flac, flac_pid, force=True)
            self._terminate(self._arec, arec_pid, force=True)

            if self._err:
                try:
                    self._err.close()
                except Exception:
                    pass

            # 4. publish atomically (same filesystem -> rename is atomic)
            tmp, final = st.get("tmp"), st.get("final")
            if tmp and final and os.path.isfile(tmp):
                try:
                    os.replace(tmp, final)
                except OSError:
                    pass

            self._arec = self._flac = self._err = None
            self._clear_state()

    # ---- termination helpers -----------------------------------------
    def _terminate(self, proc: subprocess.Popen | None, pid: int | None, force: bool = False) -> None:
        sig = signal.SIGKILL if force else signal.SIGTERM
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.send_signal(sig)
                except OSError:
                    pass
        elif self._alive(pid):
            try:
                os.kill(pid, sig)
            except OSError:
                pass

    def _wait(self, proc: subprocess.Popen | None, pid: int | None, timeout: float) -> None:
        if proc is not None:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
            return
        deadline = time.time() + timeout
        while time.time() < deadline and self._alive(pid):
            time.sleep(0.1)

    def _read_err(self) -> str:
        try:
            return self._err_path.read_text(errors="replace").strip().splitlines()[-1]
        except (OSError, IndexError):
            return ""

    # ---- misc helpers -------------------------------------------------
    def _make_name(self, label: str | None) -> str:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        if label:
            slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
            if slug:
                stamp = f"{stamp}_{slug}"
        return f"{stamp}.flac"

    def _disk_free_gb(self) -> float | None:
        path = self.mixes_dir if self.mixes_dir.exists() else Path("/")
        try:
            return round(shutil.disk_usage(path).free / 1e9, 1)
        except OSError:
            return None

    def _recent(self) -> list:
        if not self.mixes_dir.exists():
            return []
        files = []
        for p in self.mixes_dir.rglob("*"):
            if p.suffix.lower() not in AUDIO_EXTS:
                continue
            rel = p.relative_to(self.mixes_dir)
            if rel.parts and rel.parts[0] in _SKIP_DIRS:
                continue
            if p.is_file():
                files.append((p, rel))
        files.sort(key=lambda t: t[0].stat().st_mtime, reverse=True)
        out = []
        for p, rel in files[: self.recent_count]:
            stat = p.stat()
            out.append({
                "name": p.name,
                "path": str(rel),      # relative to mixes_dir, used by /media & /api/play
                "size_mb": round(stat.st_size / 1e6, 1),
                "mtime": int(stat.st_mtime),
            })
        return out
