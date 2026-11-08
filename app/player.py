#!/usr/bin/env python3
import grp
from display import Display
import device
from language import translate
import ipaddress
import pwd
import secrets
import json
import os
from pathlib import Path
import random
import socket
import stat
import subprocess
import time

VIDEOS = Path('/home/admin/desk-screen/videos')
FB = Path('/dev/fb1')
CONTROL = Path('/run/desk-screen-control/player.sock')
RESCUE_DONE = Path('/run/javiten-ip-shown')
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def local_ip():
    try:
        interfaces = json.loads(subprocess.check_output(
            ['/usr/bin/ip', '-j', '-4', 'addr', 'show', 'up', 'scope', 'global'],
            timeout=1))
        for name in ('wlan0', 'eth0'):
            for interface in interfaces:
                if interface['ifname'] == name and interface['addr_info']:
                    return str(ipaddress.IPv4Address(interface['addr_info'][0]['local']))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def device_config():
    try:
        return device.load()
    except (OSError, ValueError):
        return None


def show_rescue(address):
    config = device_config()
    language = (config or {}).get("language", "en")
    def line(text, y, size, color):
        return (f'drawtext=fontfile={FONT}:text={text}:fontsize={size}:'
                f'fontcolor={color}:x=(w-tw)/2:y={y}')
    filters = ','.join((line(device.name(config), 52, 26, '0x8ae7ba'),
                        line(address or translate('Conectando...', language), 129, 39, 'white'),
                        line(translate('Panel web / Puerto 8080', language), 218, 21, '0x8ae7ba'),
                        'format=rgb565le'))
    try:
        subprocess.run(
            ['/usr/bin/ffmpeg', '-nostdin', '-loglevel', 'error', '-f', 'lavfi',
             '-i', 'color=c=0x0a1510:s=480x320', '-vf', filters,
             '-frames:v', '1', '-f', 'fbdev', str(FB)], timeout=6, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def available(path):
    return path.suffix.lower() == '.mp4' and not path.is_symlink() and path.is_file()


class Player:
    def __init__(self):
        self.display = Display(self.apply_display)
        self.process = None
        self.airplay = None
        self.airplay_pin = None
        self.current = None
        self.last = None
        self.queue = []
        self.pending = None
        self.queued = None
        self.loop = None
        self.rescue_until = None
        self.rescue_check = 0
        self.rescue_address = ''
        self.ready = None
        self.started = time.monotonic()
        self.next_at = self.started

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None
            self.current = None

    def apply_display(self, power):
        if power:
            self.next_at = 0
            return
        self.stop()
        self.stop_airplay(resume=False)
        subprocess.run(
            ['/usr/bin/ffmpeg', '-nostdin', '-loglevel', 'error', '-f', 'lavfi',
             '-i', 'color=black:s=480x320', '-vf', 'format=rgb565le',
             '-frames:v', '1', '-f', 'fbdev', str(FB)], timeout=2, check=True)

    def tick(self):
        self.display.tick()
        if not self.display.desired(time.time()):
            return
        now = time.monotonic()
        if self.airplay is not None:
            if self.airplay.poll() is None:
                return
            self.airplay = None
            self.airplay_pin = None
        if self.ready is None:
            if FB.exists() or now - self.started >= 90:
                self.ready = now + 10
            return
        if now < self.ready:
            return
        if not RESCUE_DONE.exists() and FB.exists():
            if self.rescue_until is None:
                self.rescue_until = now + 60
            if now < self.rescue_until:
                if now >= self.rescue_check:
                    self.rescue_check = now + 2
                    address = local_ip()
                    if address != self.rescue_address and show_rescue(address):
                        self.rescue_address = address
                return
            RESCUE_DONE.touch()
        if self.process is not None:
            if self.process.poll() is None:
                return
            self.process = None
            self.current = None
            self.next_at = now + 1
        if now < self.next_at or not FB.exists():
            return
        path = self.pending
        self.pending = None
        if path is None and self.loop is not None:
            if available(self.loop):
                path = self.loop
            else:
                self.loop = None
        if path is None and self.queued is not None:
            path, self.queued = self.queued, None
        if path is None:
            if not self.queue:
                self.queue = [p for p in VIDEOS.iterdir() if available(p)]
                random.shuffle(self.queue)
            if len(self.queue) > 1 and self.queue[0] == self.last:
                self.queue[0], self.queue[1] = self.queue[1], self.queue[0]
            if self.queue:
                path = self.queue.pop(0)
        if path is None:
            self.next_at = now + 10
            return
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return
            self.process = subprocess.Popen(
                ['/usr/bin/ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                 '-re', '-protocol_whitelist', 'file,pipe', '-f', 'mov',
                 '-i', f'/proc/self/fd/{fd}',
                 '-vf', 'fps=20,scale=480:320:force_original_aspect_ratio=increase,crop=480:320,format=rgb565le',
                 '-f', 'fbdev', str(FB)], pass_fds=(fd,))
        finally:
            os.close(fd)
        self.last = path
        self.current = path.name

    def status(self):
        return {'ok': True, 'screen': self.display.status(), 'current': self.current,
                'loop': self.loop.name if self.loop else None,
                'queued': self.queued.name if self.queued else None,
                'airplay': self.airplay is not None, 'pin': self.airplay_pin}

    def start_airplay(self):
        if self.airplay is not None:
            return self.status()
        if not FB.exists() or not RESCUE_DONE.exists():
            return {'ok': False, 'error': 'Esperá a que termine el arranque.'}
        self.stop()
        pin = str(1000 + secrets.randbelow(9000))
        account = pwd.getpwnam('admin')
        try:
            self.airplay = subprocess.Popen(
                ['/usr/bin/uxplay', '-n', device.name(device_config()), '-nh', '-pin', pin,
                 '-s', '480x320', '-fps', '20', '-avdec', '-as', '0',
                 '-vs', 'videoscale add-borders=true ! video/x-raw,width=480,height=320,pixel-aspect-ratio=1/1 ! videoconvert ! video/x-raw,format=RGB16 ! fbdevsink device=/dev/fb1'],
                stdin=subprocess.DEVNULL, user=account.pw_uid, group=account.pw_gid,
                extra_groups=[grp.getgrnam('video').gr_gid],
                env={**os.environ, 'HOME': account.pw_dir})
        except OSError:
            return {'ok': False, 'error': 'No se pudo iniciar AirPlay.'}
        self.airplay_pin = pin
        return self.status()

    def stop_airplay(self, resume=True):
        if self.airplay is not None:
            self.airplay.terminate()
            try:
                self.airplay.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.airplay.kill()
                self.airplay.wait()
            self.airplay = None
            self.airplay_pin = None
            self.next_at = 0
            if resume:
                self.tick()
        return self.status()

    def command(self, request):
        action = request.get('action')
        if action in ('screen-on', 'screen-off', 'screen-schedule'):
            return self.display.command(request)
        if action == 'status':
            return self.status()
        if action == 'airplay-on' and not self.display.desired(time.time()):
            return {'ok': False, 'error': 'Encendé la pantalla para usar AirPlay.'}
        if action == 'airplay-on':
            return self.start_airplay()
        if action == 'airplay-off':
            return self.stop_airplay()
        if self.airplay is not None:
            return {'ok': False, 'error': 'Desactivá AirPlay para controlar los videos locales.'}
        if action in ('play', 'loop', 'queue'):
            name = request.get('name')
            if not isinstance(name, str) or not name or '/' in name or '\x00' in name:
                return {'ok': False}
            path = VIDEOS / name
            if not available(path):
                return {'ok': False}
            if action == 'queue':
                self.queued = path
                self.loop = None
                self.next_at = 0
                return self.status()
            self.loop = path if action == 'loop' else None
            if action == 'loop' and self.current == name:
                return self.status()
            self.pending = path
        elif action == 'random':
            self.loop = None
            self.queued = None
            return self.status()
        elif action == 'next':
            self.loop = None
            self.pending = None
        else:
            return {'ok': False}
        self.stop()
        self.next_at = 0
        self.tick()
        return self.status()


def main():
    player = Player()
    CONTROL.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(CONTROL))
        os.chown(CONTROL, 0, grp.getgrnam('admin').gr_gid)
        CONTROL.chmod(0o660)
        server.listen(8)
        server.settimeout(0.25)
        while True:
            player.tick()
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            with conn:
                conn.settimeout(0.5)
                try:
                    with conn.makefile('rb') as stream:
                        request = json.loads(stream.readline(2048))
                    if isinstance(request, dict):
                        conn.sendall(json.dumps(player.command(request)).encode() + b'\n')
                except (OSError, ValueError):
                    continue


if __name__ == '__main__':
    main()
