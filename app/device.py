"""Persistent identity and web administrator for Desk Screen."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

STATE = Path(os.environ.get('DESK_SCREEN_CONFIG', '/var/lib/desk-screen-panel/device.json'))
DEFAULT_NAME = 'Desk Screen'
LANGUAGES = ('en', 'es')


def load():
    try:
        fd = os.open(STATE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('Invalid device configuration file.')
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ValueError('Device configuration file is too large.')
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ValueError('Invalid device configuration.')
        return config
    except FileNotFoundError:
        return None


def valid_name(name):
    # Keep the name safe for the FFmpeg drawtext filter as well as AirPlay.
    return isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,31}', name)


def name(config, default=DEFAULT_NAME):
    value = (config or {}).get('name')
    return value if valid_name(value) else default


def password_hash(password, salt):
    return hashlib.scrypt(password, salt=salt, n=16384, r=8, p=1).hex()


def credentials(device_name, user, password, language):
    salt = secrets.token_bytes(16)
    return dict(name=device_name, user=user, language=language, salt=salt.hex(),
                password_hash=password_hash(password, salt))


def save(config):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=STATE.parent,
                                     delete=False) as target:
        temp = Path(target.name)
        try:
            with target:
                json.dump(config, target)
                target.flush()
                os.fsync(target.fileno())
            temp.replace(STATE)
        finally:
            temp.unlink(missing_ok=True)
