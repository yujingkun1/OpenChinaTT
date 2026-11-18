# ChinaTT point exchange table. All members start at 1500; no external ratings sync.
BANDS = [(12,8,8),(37,7,10),(62,6,13),(87,5,16),(112,4,20),(137,3,25),(162,2,30),(187,2,35),(212,1,40),(237,1,45),(float('inf'),0,50)]
SOURCE = 'https://kaiqiuwang.cc/home/space-24-do-blog-id-1124.html'

def exchange(winner_rating, loser_rating):
    gap = abs(winner_rating-loser_rating)
    for upper, expected, upset in BANDS:
        if gap <= upper:
            return expected if winner_rating >= loser_rating else upset

def replay(c):
    c.execute('UPDATE users SET rating=1500')
    c.execute('DELETE FROM rating_log')
    for m in c.execute("SELECT * FROM matches WHERE status='completed' AND settled_at IS NOT NULL ORDER BY confirmed_at,id").fetchall():
        settle(c, m)

def settle(c, m):
    if m['winner_id'] is None:
        return
    a = c.execute('SELECT rating FROM users WHERE id=?', (m['a_id'],)).fetchone()['rating']
    b = c.execute('SELECT rating FROM users WHERE id=?', (m['b_id'],)).fetchone()['rating']
    a_win = m['winner_id']==m['a_id']
    delta = exchange(a if a_win else b, b if a_win else a) * (1 if a_win else -1)
    c.execute('UPDATE matches SET a_before=?,b_before=?,a_delta=?,b_delta=? WHERE id=?', (a,b,delta,-delta,m['id']))
    for uid,before,d in [(m['a_id'],a,delta),(m['b_id'],b,-delta)]:
        c.execute('UPDATE users SET rating=? WHERE id=?',(before+d,uid))
        c.execute('INSERT INTO rating_log(user_id,match_id,before_rating,delta,after_rating) VALUES(?,?,?,?,?)',(uid,m['id'],before,d,before+d))


def settle_due(c):
    from datetime import datetime,timezone
    from zoneinfo import ZoneInfo
    midnight=datetime.now(ZoneInfo('Asia/Shanghai')).replace(hour=0,minute=0,second=0,microsecond=0)
    cutoff=midnight.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    # Event reports require only one scorer. Finalize earlier reports in this
    # transaction before settling them, using their report time for ordering.
    from app import events
    pending=c.execute("SELECT m.id,m.reported_at FROM matches m JOIN event_matches x ON x.match_id=m.id JOIN events e ON e.id=x.event_id WHERE m.status='reported' AND m.reported_at < ? AND e.status!='cancelled' ORDER BY m.reported_at,m.id",(cutoff,)).fetchall()
    for m in pending:
        c.execute("UPDATE matches SET status='completed',confirmed_at=reported_at WHERE id=?",(m['id'],))
        c.execute("INSERT INTO audit(action,target) VALUES('event.auto-confirm',?)",(str(m['id']),))
        events.match_changed(c,m['id'])
    if c.execute("SELECT value FROM settings WHERE key='rating_rebuild_pending' AND value < ?",(cutoff,)).fetchone():
        replay(c)
        c.execute("DELETE FROM settings WHERE key='rating_rebuild_pending'")
    due=c.execute("SELECT * FROM matches WHERE status='completed' AND settled_at IS NULL AND confirmed_at < ? ORDER BY confirmed_at,id",(cutoff,)).fetchall()
    for m in due:
        settle(c,m)
        c.execute("UPDATE matches SET settled_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",(m['id'],))
    return len(due)


def request_rebuild(c):
    c.execute("INSERT INTO settings(key,value) VALUES('rating_rebuild_pending',strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value")
