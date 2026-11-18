import json
import logging
import mimetypes
import re
import secrets
import signal
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit,parse_qs
from wsgiref.simple_server import WSGIServer,WSGIRequestHandler,make_server
from app import api, config, db
from app.security import AppError, digest, limit, require

log = logging.getLogger('leeds-tt')
logging.basicConfig(level=logging.INFO,format='%(message)s')
COOKIE = 'leeds_tt_session'
PUBLIC = config.ROOT/'public'

def event_stream(eid):
    """Send committed event revisions. Idle streams only emit keepalive comments."""
    previous = None
    for tick in range(55):
        with db.connect() as c:
            row = c.execute('SELECT version FROM event_revisions WHERE event_id=?',(eid,)).fetchone()
        if not row:
            return
        if row['version'] != previous:
            previous = row['version']
            yield f'data: {previous}\n\n'.encode()
        elif tick % 10 == 0:
            yield b': keepalive\n\n'
        threading.Event().wait(1)

def application(environ,start_response):
    request_id = secrets.token_hex(8)
    method = environ.get('REQUEST_METHOD','GET')
    raw_path = environ.get('PATH_INFO','/')
    path = raw_path.removeprefix(config.BASE)
    headers = [('X-Request-ID',request_id),('X-Content-Type-Options','nosniff'),('Referrer-Policy','strict-origin-when-cross-origin'),('X-Frame-Options','DENY'),('Permissions-Policy','camera=(), microphone=(), geolocation=()'),('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")]
    status = 200
    try:
        require(raw_path.startswith(config.BASE+'/') or raw_path==config.BASE,'页面不存在',404)
        stream = re.fullmatch(r'/api/events/(\d+)/stream',path)
        if stream:
            require(method=='GET','方法不允许',405)
            eid=int(stream[1])
            with db.connect() as c:
                require(c.execute('SELECT 1 FROM events WHERE id=?',(eid,)).fetchone(),'赛事不存在',404)
            headers.extend([('Content-Type','text/event-stream'),('Cache-Control','no-cache, no-transform'),('X-Accel-Buffering','no')])
            start_response('200 OK',headers)
            return event_stream(eid)
        is_api = path.startswith('/api/')
        if method in ['GET','HEAD'] and re.fullmatch(r'/api/avatars/\d+/[a-f0-9]{20}\.jpg',path):
            _,_,_,pid,filename=path.split('/')
            with db.connect() as c:
                avatar=c.execute('SELECT avatar,avatar_version FROM users WHERE id=?',(int(pid),)).fetchone()
            require(avatar and avatar['avatar'] and avatar['avatar_version']==filename[:-4],'头像不存在',404)
            content=bytes(avatar['avatar'])
            headers.extend([('Content-Type','image/jpeg'),('Cache-Control','public, max-age=86400')])
        elif is_api:
            route = path[4:]
            headers.append(('Cache-Control','no-store'))
            body = {}
            session,user = None,None
            cookies = SimpleCookie()
            try:
                cookies.load(environ.get('HTTP_COOKIE',''))
                token = cookies[COOKIE].value if COOKIE in cookies else ''
            except Exception:
                token = ''
            with db.connect() as c:
                session = c.execute('SELECT * FROM sessions WHERE token=? AND expires>?',(digest(token),time.time())).fetchone() if token else None
                if session:
                    user = c.execute('SELECT * FROM users WHERE id=? AND active=1',(session['user_id'],)).fetchone()
            if method!='GET':
                require(method=='POST','方法不允许',405)
                require(environ.get('HTTP_ORIGIN')==config.ORIGIN,'请求来源无效，请刷新页面重试',403)
                require(environ.get('CONTENT_TYPE','').split(';')[0]=='application/json','请使用JSON请求',415)
                try:
                    size = int(environ.get('CONTENT_LENGTH') or '0')
                except ValueError:
                    raise AppError('请求长度无效')
                require(0<size<=(300000 if route=='/profile/avatar' else 16384),'请求体为空或过大',413)
                try:
                    body = json.loads(environ['wsgi.input'].read(size))
                except (ValueError,UnicodeError):
                    raise AppError('请求格式不正确')
                require(isinstance(body,dict),'请求格式不正确')
                ip = environ.get('HTTP_X_REAL_IP',environ.get('REMOTE_ADDR','local'))
                with db.transaction() as c:
                    limit(c,'all:'+ip,120,60)
                    if route.startswith('/auth/') and route not in ['/auth/logout','/auth/password']:
                        limit(c,'auth:'+ip,30,900)
                    if route in ['/auth/send-code','/auth/recover']:
                        limit(c,'code:'+ip,10,3600)
                if not route.startswith('/auth/') or route in ['/auth/logout','/auth/password']:
                    require(user and session,'请先登录',401)
                    require(secrets.compare_digest(environ.get('HTTP_X_CSRF_TOKEN',''),session['csrf']),'会话已更新，请刷新页面重试',403)
            result,new_token = api.handle(method,route,body,user,session,parse_qs(environ.get('QUERY_STRING','')))
            if new_token:
                cookie = SimpleCookie()
                cookie[COOKIE] = '' if new_token=='clear' else new_token
                cookie[COOKIE]['path'] = config.BASE+'/'
                cookie[COOKIE]['httponly'] = True
                cookie[COOKIE]['samesite'] = 'Strict'
                cookie[COOKIE]['max-age'] = 0 if new_token=='clear' else 14*86400
                if config.SECURE:
                    cookie[COOKIE]['secure'] = True
                headers.append(('Set-Cookie',cookie[COOKIE].OutputString()))
            content = json.dumps(result,ensure_ascii=False).encode()
            headers.append(('Content-Type','application/json; charset=utf-8'))
        else:
            require(method in ['GET','HEAD'],'方法不允许',405)
            file = PUBLIC/('index.html' if path in ['','/'] else path.lstrip('/'))
            require(file.resolve().is_relative_to(PUBLIC.resolve()) and file.is_file() and not any(p.startswith('.') for p in file.relative_to(PUBLIC).parts),'文件不存在',404)
            content = file.read_bytes()
            mime = mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
            headers.extend([('Content-Type',mime+'; charset=utf-8'),('Cache-Control','no-cache')])
    except AppError as e:
        status = e.status
        content = json.dumps({'error':e.message,'request_id':request_id},ensure_ascii=False).encode()
        headers.append(('Content-Type','application/json; charset=utf-8'))
    except Exception:
        status = 500
        log.exception(json.dumps({'event':'request.error','request_id':request_id}))
        content = json.dumps({'error':'服务暂时出错，请稍后重试','request_id':request_id},ensure_ascii=False).encode()
        headers.append(('Content-Type','application/json; charset=utf-8'))
    reasons = {200:'OK',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',405:'Method Not Allowed',409:'Conflict',413:'Payload Too Large',415:'Unsupported Media Type',429:'Too Many Requests',500:'Internal Server Error',503:'Service Unavailable'}
    headers.append(('Content-Length',str(len(content))))
    start_response(f'{status} {reasons.get(status,"Error")}',headers)
    log.info(json.dumps({'event':'request','id':request_id,'method':method,'path':raw_path,'status':status}))
    return [b'' if method=='HEAD' else content]

class Server(ThreadingMixIn,WSGIServer):
    daemon_threads = True
    request_queue_size = 64

class Handler(WSGIRequestHandler):
    def setup(self):
        self.request.settimeout(20)
        super().setup()
    def log_message(self,*args):
        pass

def main():
    db.init()
    server = make_server(config.HOST,config.PORT,application,server_class=Server,handler_class=Handler)
    def shutdown(*_):
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    log.info(json.dumps({'event':'started','port':config.PORT}))
    server.serve_forever()
    server.server_close()

if __name__=='__main__':
    main()
