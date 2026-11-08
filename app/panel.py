#!/usr/bin/env python3
import hashlib
import hmac
import html
import ipaddress
import json
import os
import stat
import socket
import subprocess
from collections import OrderedDict
from pathlib import Path
import re
import shutil
import secrets
from http.cookies import SimpleCookie, CookieError
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import device
from language import translate, render

PORT = 8080
RESERVE = 512 * 1024**2
VIDEOS = Path(os.environ['VIDEO_DIR'])
ADDR = ipaddress.IPv4Address(os.environ['BIND_IP'])
LAN = ipaddress.IPv4Network(os.environ['LAN_CIDR'], strict=False)
PRIVATE = tuple(map(ipaddress.IPv4Network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
if (not ADDR.is_unspecified and ADDR not in LAN) or not any(LAN.subnet_of(net) for net in PRIVATE):
    raise SystemExit('La IP y la subred deben pertenecer a una LAN IPv4 privada.')
LOCAL_NAME = os.environ.get('LOCAL_NAME')
CONFIG = device.load()
TOKEN_FILE = os.environ.get('TOKEN_FILE')
PASSWORD = Path(TOKEN_FILE).read_bytes().strip() if TOKEN_FILE else None
SETUP_FILE = os.environ.get('SETUP_CODE_FILE')
SETUP_CODE = Path(SETUP_FILE).read_bytes().strip() if SETUP_FILE and CONFIG is None and not PASSWORD else None
if SETUP_CODE is not None and len(SETUP_CODE) < 24:
    raise SystemExit('Setup code must contain at least 24 characters.')
SESSIONS = {}
AUTH_LOCK = threading.Lock()
SESSION_AGE = 12 * 60 * 60
NEXT_ATTEMPT = 0
LOCK = threading.Lock()
SLOTS = threading.BoundedSemaphore(16)
THUMB_LOCK = threading.Lock()
THUMB_FAILURES = OrderedDict()
THUMB_DIR = VIDEOS / '.miniaturas-panel'
TRASH = VIDEOS / '.papelera-panel'
LOGIN = Path(__file__).with_name('login.html').read_text(encoding='utf-8')
SETUP = Path(__file__).with_name('setup.html').read_text(encoding='utf-8')
PAGE = Path(__file__).with_name('panel.html').read_text(encoding='utf-8')


def clean_partial_uploads():
    count = freed = 0
    for path in VIDEOS.iterdir():
        if not re.fullmatch(r'\.subida-[a-z0-9_]{8}\.part', path.name):
            continue
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                continue
            path.unlink()
        except FileNotFoundError:
            continue
        count += 1
        freed += info.st_blocks * 512
    return {'count': count, 'freed': freed}


def thumbnail_bytes(name):
    try:
        fd = os.open(VIDEOS / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return b''
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return b''
        signature = (1, name, info.st_ino, info.st_mtime_ns, info.st_size)
        key = hashlib.sha256(repr(signature).encode()).hexdigest()
        cached = THUMB_DIR / (key + '.jpg')
        try:
            return cached.read_bytes()
        except FileNotFoundError:
            pass
        with THUMB_LOCK:
            try:
                return cached.read_bytes()
            except FileNotFoundError:
                pass
            if THUMB_FAILURES.get(key, 0) > time.monotonic():
                return b''
            try:
                result = subprocess.run(
                    ['/usr/bin/ffmpeg',
                     '-nostdin', '-loglevel', 'error', '-threads', '1',
                     '-protocol_whitelist', 'file,pipe', '-f', 'mov',
                     '-i', f'/proc/self/fd/{fd}', '-map', '0:V:0',
                     '-filter_threads', '1', '-vf', 'scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2',
                     '-frames:v', '1', '-threads', '1', '-c:v', 'mjpeg',
                     '-q:v', '5', '-f', 'image2pipe', 'pipe:1'],
                    pass_fds=(fd,), stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=20, check=True)
                if not result.stdout.startswith(b'\xff\xd8'):
                    raise ValueError('FFmpeg no devolvió un JPEG')
            except (OSError, subprocess.SubprocessError, ValueError):
                THUMB_FAILURES[key] = time.monotonic() + 300
                THUMB_FAILURES.move_to_end(key)
                if len(THUMB_FAILURES) > 128:
                    THUMB_FAILURES.popitem(last=False)
                return b''
            if shutil.disk_usage(VIDEOS).free >= RESERVE:
                temp_fd, temp = tempfile.mkstemp(dir=THUMB_DIR, suffix='.part')
                try:
                    with os.fdopen(temp_fd, 'wb') as out:
                        out.write(result.stdout)
                    os.replace(temp, cached)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
            return result.stdout
    except OSError:
        return b''
    finally:
        os.close(fd)


def warm_thumbnails():
    while True:
        try:
            for path in sorted(VIDEOS.iterdir()):
                if visible(path):
                    thumbnail_bytes(path.name)
        except OSError:
            pass
        time.sleep(60)


def player_command(action, name=None, fields=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        sock.connect('/run/desk-screen-control/player.sock')
        sock.sendall(json.dumps({**(fields or {}), 'action': action, 'name': name}).encode() + b'\n')
        with sock.makefile('rb') as stream:
            return json.loads(stream.readline(2048))


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(30)
        super().setup()

    def log_message(self, fmt, *args):
        pass

    def reply(self, status, body='', mime='text/plain', cookie=None, location=None):
        if mime == 'text/plain':
            body = translate(body, self.language())
        data = body.encode('utf-8')
        self.send_response(status)
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        if location is not None:
            self.send_header('Location', location)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; img-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def local(self):
        dest = ipaddress.IPv4Address(self.connection.getsockname()[0])
        hosts = [f'{dest}:{PORT}']
        if LOCAL_NAME:
            hosts.append(f'{LOCAL_NAME}:{PORT}')
        values = self.headers.get_all('Host', [])
        if (dest in LAN and ipaddress.IPv4Address(self.client_address[0]) in LAN
                and len(values) == 1 and values[0] in hosts):
            self.host = values[0]
            return True
        self.reply(403, 'Acceso disponible desde la IP local configurada.')
        return False

    def session(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
        except CookieError:
            return ''
        key = cookie['javiten'].value if 'javiten' in cookie else ''
        with AUTH_LOCK:
            return key if SESSIONS.get(key, 0) > time.monotonic() else ''

    def language(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            value = cookie['desk_language'].value if 'desk_language' in cookie else None
        except CookieError:
            value = None
        return value if value in device.LANGUAGES else (CONFIG or {}).get('language', 'en')

    def page(self, template, status=200, error=''):
        language = self.language()
        label = device.name(CONFIG, 'JAVITEN PI3' if PASSWORD else device.DEFAULT_NAME)
        body = render(template, language)
        body = body.replace('<!-- ERROR -->', f'<p class="error" role="alert">{html.escape(translate(error, language))}</p>' if error else '')
        body = body.replace('__DEVICE_NAME__', html.escape(label))
        self.reply(status, body, 'text/html')

    def login_page(self, status=200, error=''):
        self.page(LOGIN, status, error)

    def form(self, limit=2048):
        lengths = self.headers.get_all('Content-Length', [])
        if (self.headers.get('Transfer-Encoding') or len(lengths) != 1
                or not re.fullmatch(r'[0-9]{1,4}', lengths[0])
                or not 0 < int(lengths[0]) <= limit
                or self.headers.get_content_type() != 'application/x-www-form-urlencoded'):
            return None
        fields = urllib.parse.parse_qs(self.rfile.read(int(lengths[0])).decode('utf-8', errors='replace'))
        return {key: values[0] for key, values in fields.items() if len(values) == 1}

    def first_run(self):
        global CONFIG, NEXT_ATTEMPT
        fields = self.form()
        if fields is None:
            self.page(SETUP, 400, 'Solicitud inválida.')
            return
        label, user = fields.get('name', '').strip(), fields.get('user', '').strip()
        password = fields.get('password', '')
        code = fields.get('code', '').encode()
        with AUTH_LOCK:
            if CONFIG or PASSWORD or not SETUP_CODE:
                self.reply(404)
                return
            now = time.monotonic()
            if now < NEXT_ATTEMPT:
                self.page(SETUP, 429, 'Esperá unos segundos e intentá de nuevo.')
                return
            if not hmac.compare_digest(code, SETUP_CODE):
                NEXT_ATTEMPT = now + 2
                self.page(SETUP, 403, 'Código de instalación inválido.')
                return
            if (not device.valid_name(label) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', user)
                    or not 12 <= len(password) <= 128 or password != fields.get('confirm')):
                self.page(SETUP, 400, 'Revisá el nombre, el usuario y la contraseña.')
                return
            config = device.credentials(label, user, password.encode(), self.language())
            try:
                device.save(config)
            except OSError:
                self.page(SETUP, 500, 'No se pudo guardar la configuración.')
                return
            CONFIG = config
        self.reply(303, location='/')

    def rename_device(self):
        global CONFIG
        fields = self.form()
        label = (fields or {}).get('name', '').strip()
        if not device.valid_name(label):
            self.page(PAGE, 400, 'Nombre de dispositivo inválido.')
            return
        with AUTH_LOCK:
            config = {**(CONFIG or {}), 'name': label}
            try:
                device.save(config)
            except OSError:
                self.page(PAGE, 500, 'No se pudo guardar la configuración.')
                return
            CONFIG = config
        self.reply(303, location='/')

    def valid_login(self, user, password):
        if CONFIG and 'password_hash' in CONFIG:
            return user == CONFIG['user'] and hmac.compare_digest(
                device.password_hash(password, bytes.fromhex(CONFIG['salt'])), CONFIG['password_hash'])
        return PASSWORD is not None and user == 'admin' and hmac.compare_digest(password, PASSWORD)

    def login(self):
        global NEXT_ATTEMPT
        fields = self.form()
        if fields is None:
            self.login_page(400, 'Solicitud de ingreso inválida.')
            return
        user = fields.get('user', '')
        password = fields.get('password', '').encode()
        now = time.monotonic()
        key = ''
        with AUTH_LOCK:
            if now < NEXT_ATTEMPT:
                status, error = 429, 'Esperá unos segundos e intentá de nuevo.'
            elif not self.valid_login(user, password):
                NEXT_ATTEMPT = now + 2
                status, error = 401, 'Usuario o contraseña incorrectos.'
            else:
                for expired in [k for k, end in SESSIONS.items() if end <= now]:
                    del SESSIONS[expired]
                if len(SESSIONS) >= 128:
                    del SESSIONS[next(iter(SESSIONS))]
                key = secrets.token_urlsafe(32)
                SESSIONS[key] = now + SESSION_AGE
        if key:
            self.reply(303, cookie=f'javiten={key}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_AGE}', location='/')
        else:
            self.login_page(status, error)

    def do_GET(self):
        if not self.local():
            return
        if self.path in ('/?lang=en', '/?lang=es'):
            self.reply(303, cookie=f'desk_language={self.path[-2:]}; Path=/; SameSite=Strict; Max-Age=31536000', location='/')
            return
        if self.path == '/':
            if not CONFIG and not PASSWORD:
                self.page(SETUP) if SETUP_CODE else self.reply(503, 'Configurá el código de instalación.')
            elif self.session():
                self.page(PAGE)
            else:
                self.login_page()
            return
        if not self.session():
            self.reply(401, 'Ingresá para continuar.')
            return
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/api/player':
            self.control_player('status')
        elif url.path in ('/api/download', '/api/thumbnail'):
            names = urllib.parse.parse_qs(url.query).get('name', [])
            if len(names) != 1 or not valid(names[0]):
                self.reply(400, 'Nombre inválido.')
                return
            if url.path == '/api/thumbnail':
                self.thumbnail(names[0])
            else:
                self.download(names[0])
        elif self.path == '/api/videos':
            try:
                files = [{'name': p.name, 'size': p.stat().st_size}
                         for p in sorted(VIDEOS.iterdir()) if visible(p)]
                disk = shutil.disk_usage(VIDEOS)
            except OSError:
                self.reply(500, 'No se pudo leer la biblioteca. Volvé a cargar.')
                return
            self.reply(200, json.dumps(dict(files=files, free=disk.free, total=disk.total)), 'application/json')
        else:
            self.reply(404)

    def thumbnail(self, name):
        data = thumbnail_bytes(name)
        if not data:
            self.reply(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def download(self, name):
        try:
            fd = os.open(VIDEOS / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            self.reply(404, 'El video ya no está en la biblioteca.')
            return
        with os.fdopen(fd, 'rb') as video:
            info = os.fstat(video.fileno())
            if not stat.S_ISREG(info.st_mode):
                self.reply(404, 'El video ya no está en la biblioteca.')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Length', str(info.st_size))
            encoded = urllib.parse.quote(name, safe='')
            self.send_header('Content-Disposition', f"attachment; filename=video.mp4; filename*=UTF-8''{encoded}")
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            try:
                self.end_headers()
                shutil.copyfileobj(video, self.wfile, 256 * 1024)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

    def control_player(self, action, name=None, fields=None):
        try:
            result = player_command(action, name, fields)
        except (OSError, ValueError):
            self.reply(503, 'El reproductor no responde.')
            return
        if not result.get('ok'):
            self.reply(409 if result.get('error') else 404,
                       result.get('error', 'El video ya no está en la biblioteca.'))
            return
        screen = result.get('screen')
        if screen and screen.get('error'):
            screen['error'] = translate(screen['error'], self.language())
        self.reply(200, json.dumps(result), 'application/json')

    def do_POST(self):
        if not self.local():
            return
        if self.headers.get_all('Origin') != ['http://' + self.host]:
            self.reply(403, 'Origen inválido.')
            return
        if self.path == '/setup':
            self.first_run()
            return
        if self.path == '/login':
            self.login()
            return
        key = self.session()
        if not key:
            self.reply(401, 'Ingresá para continuar.')
            return
        if self.path == '/device':
            self.rename_device()
            return
        if self.headers.get_all('X-Desk-Screen') != ['1']:
            self.reply(403, 'Solicitud inválida.')
            return
        if self.path == '/logout':
            with AUTH_LOCK:
                SESSIONS.pop(key, None)
            self.reply(200, cookie='javiten=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
            return
        url = urllib.parse.urlsplit(self.path)
        if url.path in ('/api/player/screen-on', '/api/player/screen-off'):
            self.control_player(url.path.rsplit('/', 1)[1])
            return
        if url.path == '/api/player/screen-schedule':
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get('Transfer-Encoding') or len(lengths) != 1
                    or not re.fullmatch(r'[0-9]{1,4}', lengths[0])
                    or not 0 < int(lengths[0]) <= 1024
                    or self.headers.get_content_type() != 'application/json'):
                self.reply(400, 'Solicitud TFT inválida.')
                return
            try:
                fields = json.loads(self.rfile.read(int(lengths[0])))
                if not isinstance(fields, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                self.reply(400, 'Configuración TFT inválida.')
                return
            self.control_player('screen-schedule', fields=fields)
            return
        if url.path in ('/api/player/next', '/api/player/random', '/api/player/airplay-on', '/api/player/airplay-off'):
            self.control_player(url.path.rsplit('/', 1)[1])
            return
        if url.path in ('/api/player/play', '/api/player/loop', '/api/player/queue'):
            names = urllib.parse.parse_qs(url.query).get('name', [])
            if len(names) != 1 or not valid(names[0]):
                self.reply(400, 'Nombre inválido.')
                return
            self.control_player(url.path.rsplit('/', 1)[1], names[0])
            return
        if url.path == '/api/storage/cleanup':
            if not LOCK.acquire(blocking=False):
                self.reply(409, 'Esperá a que termine la operación en curso.')
                return
            try:
                result = clean_partial_uploads()
            except OSError:
                self.reply(500, 'No se pudo completar la limpieza.')
                return
            finally:
                LOCK.release()
            self.reply(200, json.dumps(result), 'application/json')
            return
        if url.path == '/api/upload/check':
            fields = urllib.parse.parse_qs(url.query)
            names, sizes = fields.get('name', []), fields.get('size', [])
            if len(names) != 1 or not valid(names[0]):
                self.reply(400, 'Usá un nombre de archivo MP4 válido.')
            elif len(sizes) != 1 or not re.fullmatch(r'[0-9]{1,12}', sizes[0]) or not int(sizes[0]):
                self.reply(400, 'El archivo está vacío o su tamaño no es válido.')
            else:
                target = VIDEOS / re.sub(r'(?i)\.mp4$', '.mp4', names[0])
                rejection = self.upload_rejection(target, int(sizes[0]))
                if rejection:
                    self.reply(*rejection)
                else:
                    self.reply(200, 'Listo para subir.')
            return
        if url.path not in ('/api/upload', '/api/delete'):
            self.reply(404)
            return
        names = urllib.parse.parse_qs(url.query).get('name', [])
        if len(names) != 1 or not valid(names[0]):
            self.reply(400, 'Nombre inválido: usá un archivo .mp4 sin barras ni caracteres de control.')
            return
        name = names[0]
        if url.path == '/api/upload':
            name = re.sub(r'(?i)\.mp4$', '.mp4', name)
        target = VIDEOS / name
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,12}', lengths[0]):
            self.reply(411, 'Se requiere un tamaño de archivo conocido.')
            return
        size = int(lengths[0])
        if url.path == '/api/upload' and size == 0:
            self.reply(400, 'El archivo no puede estar vacío.')
            return
        if not LOCK.acquire(blocking=False):
            self.reply(409, 'Ya hay una operación en curso. Probá de nuevo al terminar.')
            return
        try:
            if url.path == '/api/delete':
                if size or not visible(target):
                    self.reply(404, 'El video ya no está en la biblioteca.')
                    return
                TRASH.mkdir(mode=0o700, exist_ok=True)
                if TRASH.is_symlink():
                    raise OSError('Papelera inválida')
                slot = Path(tempfile.mkdtemp(dir=TRASH))
                target.rename(slot / name)
                self.reply(200, 'Video movido a la papelera.')
            else:
                self.upload(target, size)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except OSError:
            self.reply(500, 'No se pudo completar la operación. Revisá espacio y permisos.')
        finally:
            LOCK.release()

    def upload_rejection(self, target, size):
        if os.path.lexists(target):
            return 409, 'Ya hay un video con ese nombre. Renombralo y volvé a subirlo.'
        if shutil.disk_usage(VIDEOS).free < size + RESERVE:
            return 507, 'No hay suficiente espacio. El panel reserva 512 MiB.'
        return None

    def upload(self, target, size):
        rejection = self.upload_rejection(target, size)
        if rejection:
            self.reply(*rejection)
            return
        self.connection.settimeout(120)
        fd, tmp = tempfile.mkstemp(prefix='.subida-', suffix='.part', dir=VIDEOS)
        try:
            with os.fdopen(fd, 'wb') as out:
                remaining = size
                while remaining:
                    chunk = self.rfile.read1(min(256 * 1024, remaining))
                    if not chunk:
                        print(f"Subida incompleta: {remaining} de {size} bytes pendientes.", flush=True)
                        return
                    out.write(chunk)
                    remaining -= len(chunk)
                out.flush()
                os.fchmod(out.fileno(), 0o644)
                os.fsync(out.fileno())
            try:
                os.link(tmp, target)
            except FileExistsError:
                self.reply(409, 'Ya hay un video con ese nombre.')
                return
            self.reply(201, 'Video agregado a la biblioteca.')
        except (TimeoutError, ConnectionResetError) as exc:
            print(f"Subida interrumpida: {type(exc).__name__}.", flush=True)
            raise
        finally:
            os.unlink(tmp)


def valid(name):
    return (not name.startswith('.') and name.lower().endswith('.mp4')
            and len(name.encode('utf-8')) <= 240
            and not any(c in '/\\' or ord(c) < 32 or ord(c) == 127 for c in name))


def visible(p):
    return valid(p.name) and not p.is_symlink() and p.is_file()


class Server(ThreadingHTTPServer):
    def process_request(self, request, client_address):
        if not SLOTS.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            SLOTS.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            SLOTS.release()


if __name__ == '__main__':
    THUMB_DIR.mkdir(mode=0o700, exist_ok=True)
    if THUMB_DIR.is_symlink():
        raise SystemExit('Directorio de miniaturas inválido.')
    with Server((str(ADDR), PORT), Handler) as server:
        try:
            print('Limpieza de subidas al arrancar:', clean_partial_uploads(), flush=True)
        except OSError as error:
            print('No se pudo limpiar al arrancar:', error, flush=True)
        threading.Thread(target=warm_thumbnails, daemon=True).start()
        server.serve_forever()
