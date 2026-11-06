"""HTTP boundary and real generator integration; no external server required."""
import base64
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from PIL import Image
import trimesh
from web.server import BoundedServer, Handler, Service, SPECS, validate


class WebTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.service=Service(self.temp.name,'test-password-long-enough','http://localhost',timeout=90)
        self.server=BoundedServer(('127.0.0.1',0),Handler); self.server.service=self.service
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'; self.cookie=''; self.csrf=''
    def tearDown(self):
        for ident in self.service.jobs: self.service.cancel(ident)
        for _ in range(100):
            if not any(j['process'] for j in self.service.jobs.values()): break
            time.sleep(.05)
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()
    def request(self,path,data=None,**headers):
        h={'Cookie':self.cookie,'Origin':'http://localhost','X-CSRF-Token':self.csrf,'Content-Type':'application/json'};h.update(headers)
        req=urllib.request.Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers=h)
        try:r=urllib.request.urlopen(req,timeout=10)
        except urllib.error.HTTPError as e:r=e
        payload=r.read(); status=r.status; headers=r.headers; r.close(); return status,payload,headers
    def login(self):
        code,body,headers=self.request('/api/login',{'password':self.service.password})
        self.assertEqual(code,200);self.cookie=headers['Set-Cookie'].split(';')[0];self.csrf=json.loads(body)['csrf']
    def settings(self):
        controls={k:v[3] for k,v in SPECS.items()};controls.update(resolution=40,source_diameter=0,box_y=14)
        return dict(version=1,controls=controls,invert=False,light_model='led',preview_pixels=300)
    def wait(self,ident):
        for _ in range(900):
            code,body,_=self.request('/api/jobs/'+ident);self.assertEqual(code,200);job=json.loads(body)
            if job['status'] not in ('running','canceling'):return job
            time.sleep(.1)
        self.fail('Job did not finish')
    def test_auth_validation_and_paths(self):
        self.assertEqual(self.request('/api/config')[0],401)
        self.assertEqual(self.request('/api/login',{'password':self.service.password},Origin='https://evil.example')[0],403)
        self.assertEqual(self.request('/api/login',{'password':'wrong'})[0],401)
        self.login()
        self.assertEqual(self.request('/api/jobs',{},**{'X-CSRF-Token':'wrong'})[0],403)
        self.assertEqual(self.request('/api/jobs',{'settings':self.settings(),'image':'nonsense'})[0],400)
        self.assertEqual(self.request('/api/jobs/no/../../requirements.txt')[0],404)
        self.assertEqual(self.request('/../requirements.txt')[0],404)
        settings=self.settings();settings['controls']['smoothing']=float('nan')
        self.assertEqual(self.request('/api/jobs',{'settings':settings})[0],400)
        settings=self.settings();settings['preview_pixels']=300.0;self.assertIs(type(validate(settings)['preview_pixels']),int)
        self.assertEqual(self.request('/api/jobs',{'settings':self.settings()},**{'Content-Length':str(15*1024*1024)})[0],413)
    def test_real_generation_download_and_cancel(self):
        self.login();im=Image.new('RGBA',(64,64),(0,0,0,0));im.paste((0,0,0,255),(5,5,20,40));buffer=io.BytesIO();im.save(buffer,format='PNG')
        code,body,_=self.request('/api/jobs',{'settings':self.settings(),'image':base64.b64encode(buffer.getvalue()).decode()})
        self.assertEqual(code,202);ident=json.loads(body)['id']
        self.assertEqual(self.request('/api/jobs',{'settings':self.settings()})[0],409)
        job=self.wait(ident);self.assertEqual(job['status'],'done',job)
        self.assertTrue(job['report']['watertight']);self.assertEqual(job['report']['components'],1)
        self.assertNotIn(self.temp.name,json.dumps(job))
        code,body,_=self.request(f'/api/jobs/{ident}/input.png');self.assertEqual(code,200)
        with Image.open(io.BytesIO(body)) as image:self.assertEqual(image.getpixel((0,0))[3],0)
        code,body,_=self.request(f'/api/jobs/{ident}/radial-box.stl');self.assertEqual(code,200)
        self.assertTrue(trimesh.load_mesh(io.BytesIO(body),file_type='stl').is_volume)
        code,body,_=self.request(f'/api/jobs/{ident}/print-k1.zip');self.assertEqual(code,200)
        with zipfile.ZipFile(io.BytesIO(body)) as z:self.assertEqual(len([p for p in z.namelist() if p.endswith('.stl') and '/' not in p]),6)
        for name in ['projection-point.png','projection-finite.png','projection-lighting.png','wall-1.stl']:
            self.assertEqual(self.request(f'/api/jobs/{ident}/{name}')[0],200)
        self.assertEqual(self.request(f'/api/jobs/{ident}/request.json')[0],404)
        code,body,_=self.request('/api/jobs',{'settings':self.settings()});other=json.loads(body)['id']
        self.assertEqual(self.request(f'/api/jobs/{other}/cancel',{})[0],200)
        self.assertEqual(self.wait(other)['status'],'canceled')
        self.assertEqual(self.request(f'/api/jobs/{ident}/radial-box.stl')[0],200)
    def test_generation_without_bridges(self):
        self.login()
        image = Image.new('L', (200, 200), 255)
        from PIL import ImageDraw
        ImageDraw.Draw(image).ellipse((80, 10, 120, 35), fill=0)
        buffer = io.BytesIO(); image.save(buffer, format='PNG')
        settings = self.settings()
        settings['supports'] = False
        code, body, _ = self.request('/api/jobs', {'settings': settings,
            'image': base64.b64encode(buffer.getvalue()).decode()})
        self.assertEqual(code, 202)
        ident = json.loads(body)['id']
        job = self.wait(ident)
        self.assertEqual(job['status'], 'done', job)
        self.assertEqual(job['report']['bridges_per_side'], [0, 0, 0, 0])
        self.assertGreater(job['report']['components'], 1)
        self.assertTrue(job['report']['watertight'])
        self.assertEqual(self.request(f'/api/jobs/{ident}/print-k1.zip')[0], 200)
        report = json.loads((Path(self.temp.name)/ident/'report.json').read_text())
        self.assertEqual(report['settings']['min_feature'], 0)
        self.assertFalse(report['settings']['supports'])

    def test_timeout(self):
        self.service.timeout=.001;self.login()
        code,body,_=self.request('/api/jobs',{'settings':self.settings()});ident=json.loads(body)['id']
        job=self.wait(ident);self.assertEqual(job['status'],'error');self.assertIn('время',job['error'])

if __name__=='__main__':unittest.main()
