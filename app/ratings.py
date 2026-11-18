from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from heapq import merge
from zoneinfo import ZoneInfo

# ChinaTT point exchange table. All members start at 1500; no external ratings sync.
BANDS = [(12,8,8),(37,7,10),(62,6,13),(87,5,16),(112,4,20),(137,3,25),(162,2,30),(187,2,35),(212,1,40),(237,1,45),(float('inf'),0,50)]
SOURCE = 'https://kaiqiuwang.cc/home/space-24-do-blog-id-1124.html'
SHANGHAI = ZoneInfo('Asia/Shanghai')
INACTIVITY_PENALTY = 20

def exchange(winner_rating, loser_rating):
    gap = abs(winner_rating-loser_rating)
    for upper, expected, upset in BANDS:
        if gap <= upper:
            return expected if winner_rating >= loser_rating else upset

def utc_text(timestamp):
    return timestamp.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')

def inactive_dues(c, cutoff):
    """Collect full calendar months without a completed, played match."""
    activity = defaultdict(list)
    queries = (
        ("SELECT a_id,b_id,confirmed_at FROM matches WHERE status='completed' AND winner_id IS NOT NULL AND forfeit_reason='' AND confirmed_at < ?", 2),
        ("SELECT a1,a2,b1,b2,confirmed_at FROM doubles_matches WHERE status='completed' AND confirmed_at < ?", 4),
    )
    for query, participants in queries:
        for row in c.execute(query,(cutoff,)):
            at = datetime.fromisoformat(row['confirmed_at']).astimezone(SHANGHAI)
            for uid in row[:participants]:
                activity[uid].append(at)

    boundary = datetime.fromisoformat(cutoff).astimezone(SHANGHAI)
    dues = set()
    for user in c.execute('SELECT id,created_at FROM users'):
        anchor = datetime.fromisoformat(user['created_at']).astimezone(SHANGHAI)
        for end in [*sorted(set(activity[user['id']])), boundary]:
            if end <= anchor:
                continue
            month_offset = 1
            while True:
                year, month = divmod(anchor.year * 12 + anchor.month - 1 + month_offset, 12)
                anniversary = anchor.replace(year=year, month=month+1, day=min(anchor.day,monthrange(year,month+1)[1]))
                if anniversary > end:
                    break
                midnight = anniversary.replace(hour=0,minute=0,second=0,microsecond=0)
                if anniversary > midnight:
                    midnight += timedelta(days=1)
                dues.add((user['id'],utc_text(midnight)))
                month_offset += 1
            anchor = end
    return dues

def replay(c, dues=None):
    if dues is None:
        dues = [tuple(row) for row in c.execute('SELECT user_id,due_at FROM inactivity_log')]
    c.execute('UPDATE users SET rating=1500')
    c.execute('DELETE FROM rating_log')
    c.execute('DELETE FROM inactivity_log')
    matches = c.execute("SELECT * FROM matches WHERE status='completed' AND settled_at IS NOT NULL ORDER BY confirmed_at,id").fetchall()
    match_changes = ((m['confirmed_at'],1,m['id'],m) for m in matches)
    deductions = ((at,0,uid,None) for uid,at in sorted(dues,key=lambda pair:(pair[1],pair[0])))
    for at, _, uid, match in merge(match_changes,deductions):
        if match is not None:
            settle(c,match)
        else:
            before = c.execute('SELECT rating FROM users WHERE id=?',(uid,)).fetchone()['rating']
            after = before-INACTIVITY_PENALTY
            c.execute('UPDATE users SET rating=? WHERE id=?',(after,uid))
            c.execute('INSERT INTO inactivity_log VALUES(?,?,?,?,?)',(uid,at,before,-INACTIVITY_PENALTY,after))

def settle(c, m):
    if m['winner_id'] is None or m['forfeit_reason']:
        c.execute('UPDATE matches SET a_before=NULL,b_before=NULL,a_delta=0,b_delta=0 WHERE id=?',(m['id'],))
        return
    a = c.execute('SELECT rating FROM users WHERE id=?', (m['a_id'],)).fetchone()['rating']
    b = c.execute('SELECT rating FROM users WHERE id=?', (m['b_id'],)).fetchone()['rating']
    a_win = m['winner_id']==m['a_id']
    delta = exchange(a if a_win else b, b if a_win else a) * (1 if a_win else -1)
    c.execute('UPDATE matches SET a_before=?,b_before=?,a_delta=?,b_delta=? WHERE id=?', (a,b,delta,-delta,m['id']))
    for uid,before,d in [(m['a_id'],a,delta),(m['b_id'],b,-delta)]:
        c.execute('UPDATE users SET rating=? WHERE id=?',(before+d,uid))
        c.execute('INSERT INTO rating_log(user_id,match_id,before_rating,delta,after_rating) VALUES(?,?,?,?,?)',(uid,m['id'],before,d,before+d))


def settle_due(c, now=None):
    midnight=(now or datetime.now(SHANGHAI)).astimezone(SHANGHAI).replace(hour=0,minute=0,second=0,microsecond=0)
    cutoff=utc_text(midnight)
    # Event reports require only one scorer. Finalize earlier reports in this
    # transaction before settling them, using their report time for ordering.
    from app import events
    pending=c.execute("SELECT m.id,m.reported_at FROM matches m JOIN event_matches x ON x.match_id=m.id JOIN events e ON e.id=x.event_id WHERE m.status='reported' AND m.reported_at < ? AND e.status!='cancelled' ORDER BY m.reported_at,m.id",(cutoff,)).fetchall()
    for m in pending:
        c.execute("UPDATE matches SET status='completed',confirmed_at=reported_at WHERE id=?",(m['id'],))
        c.execute("INSERT INTO audit(action,target) VALUES('event.auto-confirm',?)",(str(m['id']),))
        events.match_changed(c,m['id'])
    due=c.execute("SELECT * FROM matches WHERE status='completed' AND settled_at IS NULL AND confirmed_at < ? ORDER BY confirmed_at,id",(cutoff,)).fetchall()
    rebuild = c.execute("SELECT 1 FROM settings WHERE key='rating_rebuild_pending' AND value < ?",(cutoff,)).fetchone()
    assessed = c.execute("SELECT 1 FROM settings WHERE key='inactivity_assessed_through' AND value >= ?",(cutoff,)).fetchone()
    previous = {tuple(row) for row in c.execute('SELECT user_id,due_at FROM inactivity_log')}
    deductions = inactive_dues(c,cutoff) if rebuild or not assessed else previous
    replay_needed = rebuild or deductions != previous
    for m in due:
        if not replay_needed:
            settle(c,m)
        c.execute("UPDATE matches SET settled_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",(m['id'],))
    if replay_needed:
        replay(c,deductions)
    if rebuild:
        c.execute("DELETE FROM settings WHERE key='rating_rebuild_pending'")
    if not assessed:
        c.execute("INSERT INTO settings VALUES('inactivity_assessed_through',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(cutoff,))
    return len(due)


def request_rebuild(c):
    c.execute("INSERT INTO settings(key,value) VALUES('rating_rebuild_pending',strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value")
