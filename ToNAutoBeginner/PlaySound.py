import threading
import subprocess
from pathlib import Path

import config

# Volume（0.0~1.0）
sound_volume: float = config.DEFAULT_SOUND_VOLUME
_SOUND_VOLUME_LOCK = threading.Lock()

def get_sound_volume() -> float:
    with _SOUND_VOLUME_LOCK:
        return sound_volume

def set_sound_volume(volume: float):
    global sound_volume
    with _SOUND_VOLUME_LOCK:
        sound_volume = max(0.0, min(1.0, volume))

def play_sound(path: str):
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return
    
    def _play_sound():
        try:
            vol = get_sound_volume()
            subprocess.Popen(
                ["powershell", "-c",
                f'Add-Type -AssemblyName presentationcore; '
                f'$mp = New-Object System.Windows.Media.MediaPlayer; '
                f'$mp.Open([Uri]"{p.absolute()}"); '
                f'$mp.Volume = {vol}; '
                f'$mp.Play(); '
                f'Start-Sleep -s 2; '
                f'$mp.Close()'],
                creationflags=0x08000000
            )
        except Exception as e:
            print(f"音声再生エラー: {e}")
    threading.Thread(target=_play_sound, daemon=True).start()