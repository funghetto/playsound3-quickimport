from pathlib import Path
import platform
import atexit
from typing import Callable, Dict, Union


_PLAYSOUND_DEFAULT_BACKEND = None
_SYSTEM = platform.system()
_DOWNLOAD_CACHE = {}

class PlaysoundException(Exception):
    pass

def playsound(sound, block: bool = True, backend: Union[str, None] = None, daemon=True):
    """Play a sound file using an audio backend available in your system."""
    global _PLAYSOUND_DEFAULT_BACKEND
    if _PLAYSOUND_DEFAULT_BACKEND is None:
        _initialize_default_backend()

    if backend is None:
        _play = _PLAYSOUND_DEFAULT_BACKEND
    elif backend in _BACKEND_MAPPING:
        _play = _BACKEND_MAPPING[backend]
    else:
        raise PlaysoundException(f"Unknown backend: {backend}. Available backends: {', '.join(AVAILABLE_BACKENDS)}")

    path = _prepare_path(sound)
    if block:
        _play(path) # type: ignore
    else:
        from threading import Thread
        thread = Thread(target=_play, args=(path,), daemon=daemon)
        thread.start()
        return thread

def _download_sound_from_web(link, destination):
    from urllib.request import Request, urlopen
    from ssl import create_default_context
    from certifi import where

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64)"}
    request = Request(link, headers=headers)
    context = create_default_context(cafile=where())
    with urlopen(request, context=context) as response, open(destination, "wb") as out_file:
        out_file.write(response.read())

def _prepare_path(sound) -> str:
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    if isinstance(sound, str) and sound.startswith(("http://", "https://")):
        if sound not in _DOWNLOAD_CACHE:
            sound_suffix = Path(sound).suffix
            with NamedTemporaryFile(delete=False, prefix="playsound3-", suffix=sound_suffix) as f:
                _download_sound_from_web(sound, f.name)
                _DOWNLOAD_CACHE[sound] = f.name
        sound = _DOWNLOAD_CACHE[sound]

    path = Path(sound)
    if not path.exists():
        raise PlaysoundException(f"File not found: {sound}")
    return path.absolute().as_posix()

def _select_linux_backend() -> Callable[[str], None]:
    from subprocess import run, DEVNULL
    from logging import info

    info("Selecting the best available audio backend for Linux systems.")

    try:
        run(["gst-play-1.0", "--version"], stdout=DEVNULL, stderr=DEVNULL, check=True)
        return _playsound_gst_play
    except FileNotFoundError:
        pass

    try:
        run(["ffplay", "-version"], stdout=DEVNULL, stderr=DEVNULL, check=True)
        return _playsound_ffplay
    except FileNotFoundError:
        pass

    try:
        run(["aplay", "--version"], stdout=DEVNULL, stderr=DEVNULL, check=True)
        return _playsound_alsa
    except FileNotFoundError:
        pass

    raise PlaysoundException("No suitable audio backend found. Install gstreamer or ffmpeg!")

def _playsound_gst_play(sound: str) -> None:
    from subprocess import run
    run(["gst-play-1.0", "--no-interactive", "--quiet", sound], check=True)

def _playsound_ffplay(sound: str) -> None:
    from subprocess import run, DEVNULL
    run(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", sound],
        check=True,
        stdout=DEVNULL,
    )

def _playsound_alsa(sound: str) -> None:
    from pathlib import Path
    from subprocess import run
    suffix = Path(sound).suffix
    if suffix == ".wav":
        run(["aplay", "--quiet", sound], check=True)
    elif suffix == ".mp3":
        run(["mpg123", "-q", sound], check=True)
    else:
        raise PlaysoundException(f"Backend not supported for {suffix} files")

def _playsound_gst_legacy(sound: str) -> None:
    from logging import debug as logdebug
    from urllib import request as urlrequest
    """Play a sound using gstreamer (built-in Linux)."""

    if not sound.startswith("file://"):
        sound = "file://" + urlrequest.pathname2url(sound)

    try:
        import gi
    except ImportError:
        raise PlaysoundException("PyGObject not found. Run 'pip install pygobject'")

    # Silences gi warning
    gi.require_version("Gst", "1.0")

    try:
        # Gst will be available only if GStreamer is installed
        from gi.repository import Gst
    except ImportError:
        raise PlaysoundException("GStreamer not found. Install GStreamer on your system")

    Gst.init(None)

    playbin = Gst.ElementFactory.make("playbin", "playbin")
    playbin.props.uri = sound

    logdebug("gstreamer: starting playing %s", sound)
    set_result = playbin.set_state(Gst.State.PLAYING)
    if set_result != Gst.StateChangeReturn.ASYNC:
        raise PlaysoundException("playbin.set_state returned " + repr(set_result))
    bus = playbin.get_bus()
    try:
        bus.poll(Gst.MessageType.EOS, Gst.CLOCK_TIME_NONE)
    finally:
        playbin.set_state(Gst.State.NULL)
    logdebug("gstreamer: finishing play %s", sound)


def _send_winmm_mci_command(command):
    from logging import error as logerror
    from ctypes import WinDLL, create_string_buffer, c_char_p
    winmm = WinDLL("winmm.dll")
    buffer = create_string_buffer(255)
    error_code = winmm.mciSendStringA(c_char_p(command.encode()), buffer, 254, 0)
    if error_code:
        logerror("MCI error code: %s", error_code)
    return buffer.value


def _playsound_mci_winmm(sound: str) -> None:
    """Play a sound utilizing windll.winmm."""

    # Select a unique alias for the sound
    from logging import debug as logdebug
    from uuid import uuid4
    alias = str(uuid4())
    logdebug("winmm: starting playing %s", sound)
    _send_winmm_mci_command(f'open "{sound}" type mpegvideo alias {alias}')
    _send_winmm_mci_command(f"play {alias} wait")
    _send_winmm_mci_command(f"close {alias}")
    logdebug("winmm: finishing play %s", sound)


def _playsound_afplay(sound: str) -> None:
    """Uses afplay utility (built-in macOS)."""
    from subprocess import run
    run(["afplay", sound], check=True)

def _initialize_default_backend() -> None:
    global _PLAYSOUND_DEFAULT_BACKEND

    if _SYSTEM == "Windows":
        _PLAYSOUND_DEFAULT_BACKEND = _playsound_mci_winmm
    elif _SYSTEM == "Darwin":
        _PLAYSOUND_DEFAULT_BACKEND = _playsound_afplay
    else:
        _PLAYSOUND_DEFAULT_BACKEND = _select_linux_backend()

def _remove_cached_downloads(cache: Dict[str, str]) -> None:
    from os import remove
    for path in cache.values():
        remove(path)

atexit.register(_remove_cached_downloads, _DOWNLOAD_CACHE)

_BACKEND_MAPPING = {
    "afplay": _playsound_afplay,
    "alsa_mpg123": _playsound_alsa,
    "ffplay": _playsound_ffplay,
    "gst_play": _playsound_gst_play,
    "gst_legacy": _playsound_gst_legacy,
    "mci_winmm": _playsound_mci_winmm
}

AVAILABLE_BACKENDS = list(_BACKEND_MAPPING.keys())
