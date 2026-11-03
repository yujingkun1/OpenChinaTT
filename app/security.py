import hashlib
import hmac
import re
import secrets
import time
import unicodedata
from app import db

class AppError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

def require(condition, message, status=400):
    if not condition:
        raise AppError(message, status)

def text(value, title, maximum=120, minimum=0):
    require(isinstance(value, str), f'请填写{title}')
    s = unicodedata.normalize('NFKC', value).strip()
    require(minimum <= len(s) <= maximum and not any(ord(c)<32 for c in s), f'{title}需为{minimum}–{maximum}个字符')
    return s

def email(value):
    s = text(value, '邮箱', 254, 5).lower()
    require(bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', s)), '请输入有效的邮箱地址')
    return s

def password(value):
    require(isinstance(value, str) and 10 <= len(value) <= 128, '密码需为10–128个字符')
    return value

def digest(s):
    return hashlib.sha256(s.encode()).hexdigest()

def hash_password(p):
    salt = secrets.token_hex(16)
    value = hashlib.scrypt(p.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f'scrypt${salt}${value}'

def check_password(p, stored):
    if not isinstance(p,str) or len(p)>128:
        return False
    try:
        _, salt, value = stored.split('$')
        return hmac.compare_digest(hashlib.scrypt(p.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex(), value)
    except (ValueError, TypeError):
        return False

def limit(c, key, count, seconds):
    now = time.time()
    c.execute('DELETE FROM rate_limits WHERE reset_at < ?', (now,))
    c.execute('INSERT INTO rate_limits(key,count,reset_at) VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, now+seconds))
    row = c.execute('SELECT count FROM rate_limits WHERE key=?', (key,)).fetchone()
    if row['count'] > count:
        # Preserve failed-request counters even when the caller rolls back.
        c.commit()
        raise AppError('操作过于频繁，请稍后再试', 429)

def new_session(c, user_id):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    c.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
    c.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), user_id, csrf, time.time()+14*86400))
    return token, csrf
