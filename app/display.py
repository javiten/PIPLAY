"""TFT power and daily schedule; called by the privileged player loop."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Display:
    def __init__(self, apply, state=Path('/var/lib/desk-screen-display/control.json'), zone=None):
        self.path, self.apply = state, apply
        if zone is None:
            with open('/etc/localtime', 'rb') as source:
                zone = ZoneInfo.from_file(source)
        self.system_zone = self.zone = zone
        self.system_zone_name = zone.key or str(zone)
        if not zone.key:
            localtime = Path('/etc/localtime').resolve().as_posix()
            if '/zoneinfo/' in localtime:
                self.system_zone_name = localtime.split('/zoneinfo/', 1)[1]
            elif Path('/etc/timezone').exists():
                self.system_zone_name = Path('/etc/timezone').read_text().strip()
        self.zone_name = self.system_zone_name
        self.config = dict(enabled=False, on='08:00', off='23:00', manual=True, until=None, days=list(range(7)))
        self.error = None
        self.applied = None
        self.retry = 0
        try:
            saved = json.loads(state.read_text())
            self.validate(saved)
            if type(saved.get('manual')) is not bool or (saved.get('until') is not None and type(saved['until']) not in (int, float)):
                raise ValueError('Estado inválido')
            self.config.update(saved)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.error = 'No se pudo leer la configuración TFT.'

    @staticmethod
    def validate(c):
        if not isinstance(c, dict) or type(c.get('enabled')) is not bool:
            raise ValueError('Indicá si el horario está activado.')
        if any(not isinstance(c.get(k), str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', c[k]) for k in ('on', 'off')):
            raise ValueError('Usá horarios HH:MM válidos.')
        days = c.get('days')
        if not isinstance(days, list) or not days or any(type(day) is not int or not 0 <= day <= 6 for day in days):
            raise ValueError('Seleccioná al menos un día de la semana.')
        if c['on'] == c['off']:
            raise ValueError('Encendido y apagado deben tener horarios distintos.')

    def events(self, now):
        date = dt.datetime.fromtimestamp(now, self.zone).date()
        events = []
        for offset in range(-9, 8):
            day = date + dt.timedelta(days=offset)
            if day.weekday() not in self.config['days']:
                continue
            for key, power in (('on', True), ('off', False)):
                hour, minute = map(int, self.config[key].split(':'))
                event_day = day + dt.timedelta(days=not power and self.config['off'] < self.config['on'])
                events.append((dt.datetime.combine(event_day, dt.time(hour, minute), self.zone).timestamp(), power))
        return sorted(events)

    def desired(self, now):
        c = self.config
        if not c['enabled'] or c['until'] is not None and now < c['until']:
            return c['manual']
        return [power for at, power in self.events(now) if at <= now][-1]

    def save(self, config):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        with temp.open('w') as out:
            json.dump(config, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, self.path)
        self.config = config

    def tick(self, now=None):
        now = time.time() if now is None else now
        target = self.desired(now)
        if self.applied == target or now < self.retry:
            return
        try:
            self.apply(target)
            self.applied, self.error = target, None
        except (OSError, subprocess.SubprocessError) as exc:
            self.error = 'No se pudo cambiar la TFT: ' + str(exc)
            self.retry = now + 30

    def set_timezone(self, name):
        try:
            self.zone = ZoneInfo(name) if name else self.system_zone
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            self.zone = self.system_zone
        self.zone_name = self.zone.key or self.system_zone_name

    def status(self):
        return dict(self.config, power=self.applied, error=self.error, timezone=self.zone_name)

    def command(self, request):
        now = time.time()
        config = dict(self.config)
        try:
            if request['action'] == 'screen-schedule':
                incoming = {k: request.get(k) for k in ('enabled', 'on', 'off', 'days')}
                self.validate(incoming)
                config.update(incoming)
                config['until'] = None
                config['manual'] = self.desired(now)
            elif request['action'] in ('screen-on', 'screen-off'):
                config['manual'] = request['action'] == 'screen-on'
                config['until'] = next(at for at, _ in self.events(now) if at > now) if config['enabled'] else None
            else:
                return {'ok': False, 'error': 'Acción TFT inválida.'}
            self.save(config)
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}
        self.retry = 0
        self.tick(now)
        return {'ok': not bool(self.error), 'error': self.error, 'screen': self.status()}
