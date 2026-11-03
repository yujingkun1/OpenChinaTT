import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from app import config
from app.security import *

PUBLIC = 'id,name,role,rating,active,blade,forehand,backhand,grip,hand,bio,created_at,avatar_version,title'

def public(u):
    return {k:u[k] for k in PUBLIC.split(',')}

def private(u):
    return dict(public(u), email=u['email'], email_verified=bool(u['email_verified']))

def register(c, body):
    mail = email(body.get('email'))
    name = text(body.get('name'), '昵称', 24, 2)
    pw = password(body.get('password'))
    require(mail != config.ADMIN_EMAIL and name.casefold() not in (config.ADMIN_NAME.casefold(),'管理员','admin','administrator'), '此账号已保留，请使用其他昵称和邮箱',409)
    require(not c.execute('SELECT id FROM users WHERE email=? OR name=?',(mail,name)).fetchone(), '邮箱或昵称已被使用',409)
    recovery = secrets.token_urlsafe(24)
    uid = c.execute('INSERT INTO users(email,name,password,recovery_hash) VALUES(?,?,?,?)',(mail,name,hash_password(pw),digest(recovery))).lastrowid
    token,csrf = new_session(c,uid)
    u = c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
    return dict(user=private(u), csrf=csrf, recovery_code=recovery), token

def login(c, body):
    identity = text(body.get('identity'), '邮箱或昵称',254,2)
    p = body.get('password','')
    require(isinstance(p,str) and len(p)<=128, '邮箱、昵称或密码错误',401)
    u = c.execute('SELECT * FROM users WHERE email=? OR name=?',(identity,identity)).fetchone()
    valid = check_password(p, u['password'] if u else 'scrypt$invalid$invalid')
    require(u and valid, '邮箱、昵称或密码错误',401)
    require(u['active'], '账号已停用，请联系管理员',403)
    token,csrf = new_session(c,u['id'])
    return dict(user=private(u),csrf=csrf),token

def send_code(c, body):
    mail = email(body.get('email'))
    require(config.MAIL_READY, '邮箱验证码发信服务尚未配置，请使用密码登录',503)
    prev = c.execute('SELECT sent_at FROM codes WHERE email=?',(mail,)).fetchone()
    require(not prev or time.time()-prev['sent_at'] >= 60,'请60秒后再获取验证码',429)
    code = ''.join(secrets.choice('0123456789') for _ in range(6))
    now = time.time()
    c.execute('INSERT INTO codes VALUES(?,?,?,0,?) ON CONFLICT(email) DO UPDATE SET code_hash=excluded.code_hash,expires=excluded.expires,attempts=0,sent_at=excluded.sent_at',(mail,digest(code),now+600,now))
    msg = EmailMessage()
    msg['Subject'] = config.CLUB_NAME+' · 登录验证码'
    msg['From'] = config.SMTP_FROM
    msg['To'] = mail
    msg.set_content(f'你的{config.CLUB_NAME}登录验证码：{code}\n\n10分钟内有效，请勿分享。若非本人操作，请忽略。')
    try:
        cls = smtplib.SMTP_SSL if config.SMTP_SSL else smtplib.SMTP
        with cls(config.SMTP_HOST,config.SMTP_PORT,timeout=12) as smtp:
            if not config.SMTP_SSL:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(config.SMTP_USER,config.SMTP_PASSWORD)
            smtp.send_message(msg)
    except Exception:
        raise AppError('验证码发送失败，请稍后再试或使用密码登录',503)
    return {'message':'验证码已发送，有效期10分钟'}

def code_login(c,body):
    require(config.MAIL_READY,'邮箱验证码发信服务尚未配置，请使用密码登录',503)
    mail = email(body.get('email'))
    code = text(body.get('code'), '验证码',6,6)
    row = c.execute('SELECT * FROM codes WHERE email=?',(mail,)).fetchone()
    if not row or row['expires'] < time.time() or row['attempts']>=5 or not secrets.compare_digest(digest(code),row['code_hash']):
        c.execute('UPDATE codes SET attempts=attempts+1 WHERE email=?',(mail,))
        c.commit()
        raise AppError('验证码错误或已过期',401)
    u = c.execute('SELECT * FROM users WHERE email=?',(mail,)).fetchone()
    require(u, '请先注册昵称和密码，再使用邮箱登录',404)
    require(u['active'],'账号已停用，请联系管理员',403)
    c.execute('DELETE FROM codes WHERE email=?',(mail,))
    c.execute('UPDATE users SET email_verified=1 WHERE id=?',(u['id'],))
    token,csrf = new_session(c,u['id'])
    return dict(user=dict(private(u),email_verified=True),csrf=csrf),token

def profile(c,u,body):
    vals = [text(body.get(k,''),k,120 if k!='bio' else 200) for k in ['blade','forehand','backhand','bio']]
    require(body.get('grip') in ['横板','直板'], '请选择握拍方式')
    require(body.get('hand') in ['右手','左手'], '请选择持拍手')
    c.execute('UPDATE users SET blade=?,forehand=?,backhand=?,bio=?,grip=?,hand=? WHERE id=?',(*vals,body['grip'],body['hand'],u['id']))
    return {'user':private(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())}
