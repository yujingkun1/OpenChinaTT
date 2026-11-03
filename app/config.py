import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('TT_DATA', str(ROOT / '.work' / 'data')))
DB = DATA / 'club.sqlite3'
HOST = os.getenv('TT_HOST', '127.0.0.1')
PORT = int(os.getenv('TT_PORT', '8097'))
ORIGIN = os.getenv('TT_ORIGIN', f'http://127.0.0.1:{PORT}').rstrip('/')
BASE = os.getenv('TT_BASE', '/pingpong').rstrip('/')
SECURE = ORIGIN.startswith('https://')
ADMIN_EMAIL = os.getenv('TT_ADMIN_EMAIL', 'admin@example.com').lower()
ADMIN_NAME = os.getenv('TT_ADMIN_NAME', '管理员')
CLUB_NAME = os.getenv('TT_CLUB_NAME', '利兹乒乓')
CLUB_FULL_NAME = os.getenv('TT_CLUB_FULL_NAME', '西南交通大学利兹学院')
CLUB_ENGLISH = os.getenv('TT_CLUB_ENGLISH', 'LEEDS TABLE TENNIS CLUB')
SMTP_HOST = os.getenv('TT_SMTP_HOST', '')
SMTP_PORT = int(os.getenv('TT_SMTP_PORT', '587'))
SMTP_USER = os.getenv('TT_SMTP_USER', '')
SMTP_PASSWORD = os.getenv('TT_SMTP_PASSWORD', '')
SMTP_FROM = os.getenv('TT_SMTP_FROM', SMTP_USER)
SMTP_SSL = os.getenv('TT_SMTP_SSL', '0') == '1'
MAIL_READY = bool(SMTP_HOST and SMTP_FROM and SMTP_USER and SMTP_PASSWORD)
if not BASE.startswith('/') or not ORIGIN.startswith(('http://', 'https://')):
    raise RuntimeError('Invalid site configuration')
