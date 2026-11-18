import sqlite3
from contextlib import contextmanager
from app import config

SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_versions VALUES(1);
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 name TEXT NOT NULL UNIQUE COLLATE NOCASE, password TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin','user')),
 rating INTEGER NOT NULL DEFAULT 1500, active INTEGER NOT NULL DEFAULT 1,
 blade TEXT NOT NULL DEFAULT '', forehand TEXT NOT NULL DEFAULT '', backhand TEXT NOT NULL DEFAULT '',
 grip TEXT NOT NULL DEFAULT '横板', hand TEXT NOT NULL DEFAULT '右手', bio TEXT NOT NULL DEFAULT '',
 email_verified INTEGER NOT NULL DEFAULT 0, recovery_hash TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), csrf TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS matches(
 id INTEGER PRIMARY KEY, a_id INTEGER NOT NULL REFERENCES users(id), b_id INTEGER NOT NULL REFERENCES users(id),
 status TEXT NOT NULL DEFAULT 'invited' CHECK(status IN ('invited','accepted','reported','completed','declined','cancelled','voided')),
 best_of INTEGER NOT NULL CHECK(best_of IN (3,5,7)), venue TEXT NOT NULL, scheduled_at TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '', score_a INTEGER, score_b INTEGER, games TEXT,
 reporter_id INTEGER REFERENCES users(id), winner_id INTEGER REFERENCES users(id),
 a_before INTEGER, b_before INTEGER, a_delta INTEGER, b_delta INTEGER,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 confirmed_at TEXT, void_reason TEXT, CHECK(a_id != b_id)
);
CREATE INDEX IF NOT EXISTS matches_players_a ON matches(a_id,status);
CREATE INDEX IF NOT EXISTS matches_players_b ON matches(b_id,status);
CREATE TABLE IF NOT EXISTS doubles_matches(
 id INTEGER PRIMARY KEY, a1 INTEGER NOT NULL REFERENCES users(id), a2 INTEGER NOT NULL REFERENCES users(id),
 b1 INTEGER NOT NULL REFERENCES users(id), b2 INTEGER NOT NULL REFERENCES users(id),
 status TEXT NOT NULL DEFAULT 'invited' CHECK(status IN ('invited','accepted','reported','completed','declined','cancelled','voided')),
 best_of INTEGER NOT NULL CHECK(best_of IN (3,5,7)), venue TEXT NOT NULL,scheduled_at TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',
 score_a INTEGER,score_b INTEGER,games TEXT,reporter_id INTEGER REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),confirmed_at TEXT,void_reason TEXT,
 CHECK(a1!=a2 AND a1!=b1 AND a1!=b2 AND a2!=b1 AND a2!=b2 AND b1!=b2)
);
CREATE TABLE IF NOT EXISTS doubles_consents(
 match_id INTEGER NOT NULL REFERENCES doubles_matches(id),user_id INTEGER NOT NULL REFERENCES users(id),
 accepted INTEGER NOT NULL DEFAULT 0,confirmed INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(match_id,user_id)
);
CREATE INDEX IF NOT EXISTS doubles_status ON doubles_matches(status);
CREATE TABLE IF NOT EXISTS rating_log(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), match_id INTEGER NOT NULL REFERENCES matches(id), before_rating INTEGER NOT NULL, delta INTEGER NOT NULL, after_rating INTEGER NOT NULL, UNIQUE(user_id,match_id));
CREATE TABLE IF NOT EXISTS codes(email TEXT PRIMARY KEY COLLATE NOCASE, code_hash TEXT NOT NULL, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, sent_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY, count INTEGER NOT NULL, reset_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id), action TEXT NOT NULL, target TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
'''

class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

def connect():
    c = sqlite3.connect(config.DB, timeout=15, isolation_level=None, factory=Connection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=15000')
    return c

@contextmanager
def transaction():
    c = connect()
    try:
        c.execute('BEGIN IMMEDIATE')
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()

def init():
    config.DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript(SCHEMA)
        columns={row['name'] for row in c.execute('PRAGMA table_info(users)')}
        if 'avatar' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN avatar BLOB')
        if 'avatar_version' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN avatar_version TEXT NOT NULL DEFAULT ''")
        c.execute('INSERT OR IGNORE INTO schema_versions VALUES(2)')
        c.execute('INSERT OR IGNORE INTO schema_versions VALUES(3)')
        if 'settled_at' not in {r['name'] for r in c.execute('PRAGMA table_info(matches)')}:
            c.execute('ALTER TABLE matches ADD COLUMN settled_at TEXT')
            c.execute('UPDATE matches SET settled_at=confirmed_at WHERE id IN (SELECT match_id FROM rating_log)')
        from app.events import SCHEMA as EVENT_SCHEMA
        c.executescript(EVENT_SCHEMA)
        c.execute('INSERT OR IGNORE INTO schema_versions VALUES(4)')
        if 'title' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
            c.execute("ALTER TABLE users ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        c.execute('INSERT OR IGNORE INTO schema_versions VALUES(5)')
        if 'rating_ceiling' not in {r['name'] for r in c.execute('PRAGMA table_info(events)')}:
            c.execute('ALTER TABLE events ADD COLUMN rating_ceiling INTEGER')
        c.execute('INSERT OR IGNORE INTO schema_versions VALUES(6)')
        if 'absent_reason' not in {r['name'] for r in c.execute('PRAGMA table_info(event_entries)')}:
            c.execute("ALTER TABLE event_entries ADD COLUMN absent_reason TEXT NOT NULL DEFAULT ''")
        if 'forfeit_reason' not in {r['name'] for r in c.execute('PRAGMA table_info(matches)')}:
            c.execute("ALTER TABLE matches ADD COLUMN forfeit_reason TEXT NOT NULL DEFAULT ''")
        c.execute('INSERT OR IGNORE INTO schema_versions VALUES(7)')
        if 'reported_at' not in {r['name'] for r in c.execute('PRAGMA table_info(matches)')}:
            c.execute('ALTER TABLE matches ADD COLUMN reported_at TEXT')
            c.execute("UPDATE matches SET reported_at=coalesce((SELECT max(a.created_at) FROM audit a WHERE a.action='match.report' AND a.target=cast(matches.id AS TEXT)),strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE status='reported'")
        c.execute('INSERT OR IGNORE INTO schema_versions VALUES(8)')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS event_revisions(event_id INTEGER PRIMARY KEY REFERENCES events(id),version INTEGER NOT NULL DEFAULT 0);
        INSERT OR IGNORE INTO event_revisions(event_id) SELECT id FROM events;
        CREATE TRIGGER IF NOT EXISTS event_revision_create AFTER INSERT ON events BEGIN
          INSERT INTO event_revisions(event_id) VALUES(new.id);
        END;
        CREATE TRIGGER IF NOT EXISTS event_revision_update AFTER UPDATE ON events BEGIN
          UPDATE event_revisions SET version=version+1 WHERE event_id=new.id;
        END;
        CREATE TRIGGER IF NOT EXISTS event_revision_match AFTER UPDATE ON matches BEGIN
          UPDATE event_revisions SET version=version+1 WHERE event_id IN (SELECT event_id FROM event_matches WHERE match_id=new.id);
        END;
        CREATE TRIGGER IF NOT EXISTS event_revision_fixture AFTER INSERT ON event_matches BEGIN
          UPDATE event_revisions SET version=version+1 WHERE event_id=new.event_id;
        END;
        CREATE TRIGGER IF NOT EXISTS event_revision_entry_insert AFTER INSERT ON event_entries BEGIN
          UPDATE event_revisions SET version=version+1 WHERE event_id=new.event_id;
        END;
        CREATE TRIGGER IF NOT EXISTS event_revision_entry_update AFTER UPDATE ON event_entries BEGIN
          UPDATE event_revisions SET version=version+1 WHERE event_id=new.event_id;
        END;
        CREATE TRIGGER IF NOT EXISTS event_revision_entry_delete AFTER DELETE ON event_entries BEGIN
          UPDATE event_revisions SET version=version+1 WHERE event_id=old.event_id;
        END;
        INSERT OR IGNORE INTO schema_versions VALUES(9);
        ''')
