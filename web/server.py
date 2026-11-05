"""Single-owner web service. Run behind a TLS reverse proxy; see WEB.md."""
import base64
import hashlib
import hmac
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / 'static'
# key: label, minimum, maximum, default, step
SPECS = {
 'size': ('Основание, мм',60,300,100,1), 'height': ('Источник над стеной, мм',40,400,70,1),
 'span': ('Картинка на стене, мм',120,4500,600,10), 'thickness': ('Толщина стенок, мм',1.2,5,2,.1),
 'box_x': ('Центр короба X, %',-40,40,0,1), 'box_y': ('Центр короба Y, %',-40,40,0,1),
 'bridge': ('Перемычки, мм',.8,5,1.4,.1), 'source_diameter': ('Размер свечения, мм',0,10,2,.1),
 'beam_angle': ('Угол LED (½ силы), °',10,170,120,5), 'flux': ('Световой поток, лм',1,5000,100,10),
 'ambient': ('Свет в комнате, лк',0,1000,1,1), 'white_lux': ('Белый в превью, лк',1,10000,100,10),
 'min_feature': ('Мин. элемент, мм',.8,5,1.2,.1), 'smoothing': ('Сглаживание краёв, мм',0,2,.8,.1),
 'threshold': ('Порог чёрного',0,255,128,1), 'resolution': ('Детализация стенок',40,300,180,10)}
ARTIFACTS = {name: name for name in ['radial-box.stl','print-k1.zip','projection-point.png','projection-finite.png','projection-lighting.png','preview.png','input.png']}
ARTIFACTS.update({f'{name}.stl': f'print-k1/{name}.stl' for name in ['base','post','wall-1','wall-2','wall-3','wall-4']})


def validate(data):
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('controls'),dict):
        raise ValueError('Неверный формат настроек')
    result = {}
    for key, (_, low, high, default, _) in SPECS.items():
        value = data['controls'].get(key, default)
        if type(value) not in (int,float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'Недопустимое значение: {key}')
        if key in ('threshold','resolution'):
            if value != int(value): raise ValueError(f'Требуется целое число: {key}')
            value = int(value)
        result[key] = value
    if not 2*result['size'] <= result['span'] <= 15*result['size']:
        raise ValueError('Размер картинки должен быть от 2 до 15 размеров основания')
    if max(abs(result['box_x']),abs(result['box_y']))/100*result['span']+result['size']/2 > result['span']/2:
        raise ValueError('Короб выходит за границы картинки')
    if data.get('light_model') not in ('led','isotropic') or type(data.get('invert')) is not bool or data.get('preview_pixels') not in (300,600):
        raise ValueError('Неверные параметры освещения или превью')
    return dict(version=1,controls=result,light_model=data['light_model'],invert=data['invert'],preview_pixels=int(data['preview_pixels']))


class Service:
    def __init__(self, directory, password, origin, timeout=900):
        self.directory = Path(directory).resolve(); self.directory.mkdir(parents=True,exist_ok=True)
        self.password = password; self.origin = origin.rstrip('/'); self.timeout = timeout
        self.lock = threading.RLock(); self.jobs = {}; self.sessions = {}; self.attempts = []

    def create(self, data):
        with self.lock:
            if any(j['status'] in ('running','canceling') for j in self.jobs.values()):
                raise RuntimeError('Уже выполняется расчёт')
        settings = validate(data.get('settings'))
        encoded = data.get('image')
        image = None
        if encoded is not None:
            if not isinstance(encoded,str): raise ValueError('Неверное изображение')
            try:
                raw = base64.b64decode(encoded,validate=True)
                if len(raw)>10*1024*1024: raise ValueError('Изображение больше 10 МБ')
                with Image.open(io.BytesIO(raw)) as im:
                    if im.width*im.height > 16_000_000: raise ValueError('Изображение больше 16 мегапикселей')
                    im.load(); image = ImageOps.exif_transpose(im).convert('RGBA')
            except Exception as exc:
                raise ValueError('Не удалось прочитать изображение (лимит 10 МБ / 16 Мп)') from exc
        with self.lock:
            if any(j['status'] in ('running','canceling') for j in self.jobs.values()):
                raise RuntimeError('Уже выполняется расчёт; дождитесь окончания или отмените его')
            # Keep last eight completed jobs; never remove an active calculation.
            for ident in list(self.jobs)[:-7]:
                shutil.rmtree(self.directory/ident,ignore_errors=True); del self.jobs[ident]
            while self.jobs and sum(p.stat().st_size for p in self.directory.rglob('*') if p.is_file()) > 512*1024*1024:
                oldest = next(iter(self.jobs)); shutil.rmtree(self.directory/oldest,ignore_errors=True); del self.jobs[oldest]
            ident = secrets.token_hex(16); directory = self.directory/ident; directory.mkdir()
            if image is not None: image.save(directory/'input.png')
            job = dict(id=ident,status='running',stage='Запуск',error=None,process=None,cancel=False,created=time.time())
            self.jobs[ident] = job
            threading.Thread(target=self.run,args=(job,settings,image is not None),daemon=True).start()
            return ident

    def run(self, job, settings, has_image):
        directory = self.directory/job['id']
        cmd = [sys.executable,'-u',str(ROOT/'radial_shadow.py'),'--out',str(directory)]
        cmd += [str(directory/'input.png')] if has_image else ['--demo']
        for key,value in settings['controls'].items(): cmd += ['--'+key.replace('_','-'),str(value)]
        cmd += ['--light-model',settings['light_model'],'--preview-pixels',str(settings['preview_pixels']),'--nozzle','0.4']
        if settings['invert']: cmd.append('--invert')
        timer = None
        process = None
        try:
            with self.lock:
                if job['cancel']: job['status']='canceled'; return
                process = subprocess.Popen(cmd,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                job['process'] = process
            def expire():
                with self.lock:
                    if process.poll() is None:
                        job['timed_out']=True; process.kill()
            timer = threading.Timer(self.timeout,expire); timer.start()
            tail = []
            for line in process.stdout:
                tail.append(line); tail=tail[-30:]
                if line.startswith('STAGE:'): job['stage']=line[6:].strip()
            code = process.wait()
            with self.lock:
                if job['cancel']: job['status']='canceled'
                elif job.get('timed_out'): job.update(status='error',error='Превышено время расчёта')
                elif code: job.update(status='error',error='Расчёт не завершён: '+''.join(tail)[-1800:].replace(str(ROOT),'[app]').replace(str(self.directory),'[data]'))
                else:
                    report=json.loads((directory/'report.json').read_text())
                    # Reports from CLI contain filesystem paths; publish useful fields only.
                    job['report']={k:report[k] for k in ('watertight','components','bridges_per_side','lighting','printing','limitations')}
                    job.update(status='done',stage='Готово')
        except Exception:
            job.update(status='error',error='Внутренняя ошибка расчёта; проверьте журнал сервера')
            import traceback; traceback.print_exc()
        finally:
            if timer: timer.cancel()
            if process:
                if process.poll() is None: process.kill()
                process.wait()
                if process.stdout: process.stdout.close()
            job['process']=None

    def cancel(self, ident):
        with self.lock:
            job=self.jobs.get(ident)
            if job and job['status'] in ('running','canceling'):
                job['cancel']=True; job['status']='canceling'
                if job['process']: job['process'].kill()


class BoundedServer(ThreadingHTTPServer):
    # Prevent slow clients from creating an unbounded number of worker threads.
    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)
    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try: super().process_request(request, client_address)
        except Exception:
            self.slots.release(); raise
    def process_request_thread(self, request, client_address):
        try: super().process_request_thread(request, client_address)
        finally: self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'ShadowCube'
    def setup(self):
        super().setup(); self.connection.settimeout(15)
    @property
    def service(self): return self.server.service
    def send(self, code, payload, content_type='application/json', cookie=None):
        if not isinstance(payload,bytes): payload=json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(payload))); self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(payload)
    def session(self):
        try:
            cookie=SimpleCookie(self.headers.get('Cookie','')); key=cookie['shadow_session'].value
            value=self.service.sessions.get(key)
            if value and value['expires']>time.time(): return value
        except (KeyError,ValueError): pass
        return None
    def do_GET(self):
        path=urlsplit(self.path).path
        if path in ('/','/app.js','/style.css','/viewer.js'):
            file=STATIC/('index.html' if path=='/' else path[1:])
            return self.send(200,file.read_bytes(),mimetypes.guess_type(file)[0] or 'text/plain')
        session=self.session()
        if not session: return self.send(401,{'error':'Нужен вход'})
        if path=='/api/config': return self.send(200,dict(specs=SPECS,csrf=session['csrf']))
        parts=path.strip('/').split('/')
        if len(parts)>=3 and parts[:2]==['api','jobs']:
            job=self.service.jobs.get(parts[2])
            if not job: return self.send(404,{'error':'Результат не найден или удалён'})
            if len(parts)==3:
                return self.send(200,{k:v for k,v in job.items() if k not in ('process','cancel')})
            if len(parts)==4 and parts[3] in ARTIFACTS and job['status']=='done':
                file=self.service.directory/job['id']/ARTIFACTS[parts[3]]
                if file.is_file(): return self.send(200,file.read_bytes(),mimetypes.guess_type(file)[0] or 'application/octet-stream')
        self.send(404,{'error':'Не найдено'})
    def do_POST(self):
        # Explicit origin protects login and all mutations against cross-site requests.
        if self.headers.get('Origin') != self.service.origin:
            return self.send(403,{'error':'Недопустимый Origin; проверьте SHADOW_ORIGIN'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=14*1024*1024: return self.send(413,{'error':'Превышен размер запроса'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json': return self.send(415,{'error':'Требуется JSON'})
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict): raise ValueError('Требуется объект JSON')
        except (ValueError,TimeoutError): return self.send(400,{'error':'Неверный JSON'})
        path=urlsplit(self.path).path
        if path=='/api/login':
            with self.service.lock:
                now=time.time(); self.service.attempts=[t for t in self.service.attempts if now-t<60]
                if len(self.service.attempts)>=10: return self.send(429,{'error':'Слишком много попыток; подождите минуту'})
                self.service.attempts.append(now)
                supplied=data.get('password','')
                if not isinstance(supplied,str) or not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),hashlib.sha256(self.service.password.encode()).digest()):
                    return self.send(401,{'error':'Неверный пароль'})
                self.service.sessions={k:v for k,v in self.service.sessions.items() if v['expires']>now}
                token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
                self.service.sessions[token]=dict(expires=now+86400,csrf=csrf)
            secure='; Secure' if self.service.origin.startswith('https://') else ''
            return self.send(200,{'csrf':csrf},cookie=f'shadow_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400{secure}')
        session=self.session()
        if not session: return self.send(401,{'error':'Нужен вход'})
        if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']): return self.send(403,{'error':'Неверный CSRF token'})
        if path=='/api/jobs':
            try: return self.send(202,{'id':self.service.create(data)})
            except ValueError as exc: return self.send(400,{'error':str(exc)})
            except RuntimeError as exc: return self.send(409,{'error':str(exc)})
        parts=path.strip('/').split('/')
        if len(parts)==4 and parts[:2]==['api','jobs'] and parts[3]=='cancel':
            self.service.cancel(parts[2]); return self.send(200,{'ok':True})
        self.send(404,{'error':'Не найдено'})


def main():
    password=os.environ.get('SHADOW_PASSWORD','')
    if len(password)<16: raise SystemExit('Set SHADOW_PASSWORD to a password of at least 16 characters')
    origin=os.environ.get('SHADOW_ORIGIN','http://localhost:8080')
    if urlsplit(origin).scheme not in ('http','https') or not urlsplit(origin).netloc:
        raise SystemExit('SHADOW_ORIGIN must be a full public origin')
    service=Service(os.environ.get('SHADOW_DATA','output/web'),password,origin,int(os.environ.get('SHADOW_TIMEOUT','900')))
    # Remove only this service’s UUID job directories after restart: no stale data accumulation.
    for entry in service.directory.iterdir():
        if len(entry.name)==32 and all(c in '0123456789abcdef' for c in entry.name) and entry.is_dir(): shutil.rmtree(entry)
    server=BoundedServer((os.environ.get('SHADOW_HOST','127.0.0.1'),int(os.environ.get('SHADOW_PORT','8080'))),Handler)
    server.service=service
    def stop(signum, frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    print(f'ShadowCube listening on {server.server_address}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        for ident in list(service.jobs): service.cancel(ident)
        server.server_close()

if __name__=='__main__': main()
