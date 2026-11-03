import itertools
import random
from datetime import datetime,timezone
from app import accounts,ratings
from app.security import *
from app.matches import audit

SCHEMA='''
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',venue TEXT NOT NULL,starts_at TEXT NOT NULL,best_of INTEGER NOT NULL,group_count INTEGER NOT NULL,qualifiers INTEGER NOT NULL,description TEXT NOT NULL DEFAULT '',champion_id INTEGER REFERENCES users(id),created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),cancel_reason TEXT);
CREATE TABLE IF NOT EXISTS event_entries(id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL REFERENCES events(id),user_id INTEGER NOT NULL REFERENCES users(id),seed INTEGER,group_no INTEGER,UNIQUE(event_id,user_id));
CREATE TABLE IF NOT EXISTS event_matches(event_id INTEGER NOT NULL REFERENCES events(id),match_id INTEGER NOT NULL UNIQUE REFERENCES matches(id),phase TEXT NOT NULL,group_no INTEGER,round_no INTEGER,slot INTEGER);
CREATE TABLE IF NOT EXISTS event_bracket(event_id INTEGER NOT NULL REFERENCES events(id),round_no INTEGER NOT NULL,slot INTEGER NOT NULL,a_id INTEGER REFERENCES users(id),b_id INTEGER REFERENCES users(id),winner_id INTEGER REFERENCES users(id),match_id INTEGER REFERENCES matches(id),PRIMARY KEY(event_id,round_no,slot));
'''

def get(c,eid):
    e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
    require(e,'赛事不存在',404)
    return e

def public(c,eid):
    e=dict(get(c,eid))
    e['entries']=[dict(r) for r in c.execute('SELECT e.*,u.name,u.avatar_version,u.rating FROM event_entries e JOIN users u ON u.id=e.user_id WHERE event_id=? ORDER BY coalesce(seed,999999),e.id',(eid,))]
    e['matches']=[dict(r) for r in c.execute('''SELECT m.*,a.name a_name,b.name b_name,a.avatar_version a_avatar_version,b.avatar_version b_avatar_version,x.event_id,x.phase,x.group_no,x.round_no,x.slot FROM event_matches x JOIN matches m ON m.id=x.match_id JOIN users a ON a.id=m.a_id JOIN users b ON b.id=m.b_id WHERE x.event_id=? ORDER BY x.phase,x.group_no,x.round_no,x.slot,m.id''',(eid,))]
    e['groups']=[{'number':i,'standings':standings(c,eid,i)} for i in range(e['group_count'])] if e['status']!='open' else []
    e['bracket']=[dict(r) for r in c.execute('SELECT * FROM event_bracket WHERE event_id=? ORDER BY round_no,slot',(eid,))]
    return e

def list_events(c):
    return [dict(r) for r in c.execute('''SELECT e.*,(SELECT count(*) FROM event_entries x WHERE x.event_id=e.id) participants,u.name champion_name FROM events e LEFT JOIN users u ON u.id=e.champion_id ORDER BY e.id DESC LIMIT 100''')]

def create(c,u,b):
    title=text(b.get('title'),'赛事名称',60,2)
    kind=b.get('kind','weekly');require(kind in ['weekly','selection','open'],'请选择赛事类型')
    venue=text(b.get('venue'),'比赛地点',100,2)
    date=text(b.get('starts_at'),'比赛时间',40,10)
    try:
        dt=datetime.fromisoformat(date.replace('Z','+00:00'));require(dt.tzinfo,'时间必须包含时区');date=dt.astimezone(timezone.utc).isoformat()
    except ValueError:raise AppError('比赛时间格式错误')
    groups=b.get('group_count',2);q=b.get('qualifiers',2);best=b.get('best_of',5)
    require(type(groups)==int and 1<=groups<=16,'小组数应为1–16')
    require(type(q)==int and 1<=q<=8,'每组晋级人数应为1–8')
    require(type(best)==int and best in [3,5,7],'请选择有效赛制')
    eid=c.execute('INSERT INTO events(title,kind,venue,starts_at,best_of,group_count,qualifiers,description,created_by) VALUES(?,?,?,?,?,?,?,?,?)',(title,kind,venue,date,best,groups,q,text(b.get('description',''),'赛事说明',500),u['id'])).lastrowid
    audit(c,u['id'],'event.create',eid)
    return {'id':eid,'message':'赛事已创建，球友现在可以报名'}

def fixture(c,e,a,b,phase,group=None,round_no=None,slot=None):
    mid=c.execute("INSERT INTO matches(a_id,b_id,status,best_of,venue,scheduled_at,note) VALUES(?,?,'accepted',?,?,?,?)",(a,b,e['best_of'],e['venue'],e['starts_at'],f"赛事：{e['title']} · "+('小组赛' if phase=='group' else '淘汰赛'))).lastrowid
    c.execute('INSERT INTO event_matches VALUES(?,?,?,?,?,?)',(e['id'],mid,phase,group,round_no,slot))
    return mid

def standings(c,eid,group):
    entrants=[dict(r) for r in c.execute('SELECT e.user_id,e.seed,e.id entry_id,u.name,u.avatar_version FROM event_entries e JOIN users u ON u.id=e.user_id WHERE event_id=? AND group_no=?',(eid,group))]
    rows={p['user_id']:dict(p,played=0,wins=0,game_diff=0,point_diff=0,tie_wins=0) for p in entrants}
    ms=c.execute("SELECT m.* FROM event_matches x JOIN matches m ON m.id=x.match_id WHERE x.event_id=? AND x.group_no=? AND x.phase='group' AND m.status='completed'",(eid,group)).fetchall()
    import json
    for m in ms:
        games=json.loads(m['games'])
        pd=sum(g[0]-g[1] for g in games)
        for uid,sg,pdiff in [(m['a_id'],m['score_a']-m['score_b'],pd),(m['b_id'],m['score_b']-m['score_a'],-pd)]:
            r=rows[uid];r['played']+=1;r['wins']+=int(m['winner_id']==uid);r['game_diff']+=sg;r['point_diff']+=pdiff
    for m in ms:
        if rows[m['a_id']]['wins']==rows[m['b_id']]['wins']:rows[m['winner_id']]['tie_wins']+=1
    return sorted(rows.values(),key=lambda r:(-r['wins'],-r['tie_wins'],-r['game_diff'],-r['point_diff'],r['seed'] or 999999,r['entry_id']))

def progress(c,eid):
    e=get(c,eid)
    if e['status']!='knockout':return
    changed=True
    while changed:
        changed=False
        for row in c.execute('SELECT * FROM event_bracket WHERE event_id=? ORDER BY round_no,slot',(eid,)).fetchall():
            if row['winner_id']:continue
            if row['match_id']:
                m=c.execute('SELECT status,winner_id FROM matches WHERE id=?',(row['match_id'],)).fetchone()
                if m['status']=='completed':
                    c.execute('UPDATE event_bracket SET winner_id=? WHERE event_id=? AND round_no=? AND slot=?',(m['winner_id'],eid,row['round_no'],row['slot']));changed=True
            elif row['round_no']>1:
                children=c.execute('SELECT winner_id FROM event_bracket WHERE event_id=? AND round_no=? AND slot IN (?,?) ORDER BY slot',(eid,row['round_no']-1,row['slot']*2,row['slot']*2+1)).fetchall()
                if len(children)==2 and all(x['winner_id'] for x in children):
                    a,b=[x['winner_id'] for x in children]
                    mid=fixture(c,e,a,b,'knockout',round_no=row['round_no'],slot=row['slot'])
                    c.execute('UPDATE event_bracket SET a_id=?,b_id=?,match_id=? WHERE event_id=? AND round_no=? AND slot=?',(a,b,mid,eid,row['round_no'],row['slot']));changed=True
    final=c.execute('SELECT * FROM event_bracket WHERE event_id=? ORDER BY round_no DESC LIMIT 1',(eid,)).fetchone()
    if final and final['winner_id']:c.execute("UPDATE events SET status='completed',champion_id=? WHERE id=?",(final['winner_id'],eid))

def match_changed(c,mid):
    linked=c.execute('SELECT event_id FROM event_matches WHERE match_id=?',(mid,)).fetchone()
    if linked:progress(c,linked['event_id'])

def guard_void(c,mid):
    linked=c.execute('SELECT x.phase,e.status FROM event_matches x JOIN events e ON e.id=x.event_id WHERE x.match_id=?',(mid,)).fetchone()
    if linked:require(linked['phase']=='group' and linked['status']=='groups','此赛果已进入赛事晋级链，不能单独作废；可在赛事管理中取消整场赛事',409)

def action(c,u,eid,op,b):
    e=get(c,eid)
    if op in ['join','leave']:
        require(e['status']=='open','分组已生成，报名已结束',409)
        if op=='join':
            require(c.execute('SELECT count(*) FROM event_entries WHERE event_id=?',(eid,)).fetchone()[0]<128,'赛事人数已满')
            require(not c.execute('SELECT id FROM event_entries WHERE event_id=? AND user_id=?',(eid,u['id'])).fetchone(),'你已经报名',409)
            c.execute('INSERT INTO event_entries(event_id,user_id) VALUES(?,?)',(eid,u['id']))
        else:c.execute('DELETE FROM event_entries WHERE event_id=? AND user_id=?',(eid,u['id']))
        audit(c,u['id'],'event.'+op,eid)
        return {'message':'已报名，分组后的赛事对战将自动建立' if op=='join' else '已退出报名'}
    require(u['role']=='admin','需要管理员权限',403)
    if op=='seeds':
        require(e['status']=='open','分组后不能修改种子',409)
        seeds=b.get('seeds');require(isinstance(seeds,list) and len(seeds)<=128,'种子格式错误')
        numbers=set();uids=set()
        for s in seeds:
            require(isinstance(s,dict) and type(s.get('user_id'))==int and type(s.get('seed'))==int and 1<=s['seed']<=128,'种子编号应为1–128')
            require(s['seed'] not in numbers and s['user_id'] not in uids,'种子编号或球友重复')
            require(c.execute('SELECT id FROM event_entries WHERE event_id=? AND user_id=?',(eid,s['user_id'])).fetchone(),'只能为已报名球友设置种子')
            numbers.add(s['seed']);uids.add(s['user_id'])
        c.execute('UPDATE event_entries SET seed=NULL WHERE event_id=?',(eid,))
        for s in seeds:c.execute('UPDATE event_entries SET seed=? WHERE event_id=? AND user_id=?',(s['seed'],eid,s['user_id']))
    elif op=='draw':
        require(e['status']=='open','分组已生成',409)
        c.execute('DELETE FROM event_entries WHERE event_id=? AND user_id IN (SELECT id FROM users WHERE active=0)',(eid,))
        entries=c.execute('SELECT * FROM event_entries WHERE event_id=? ORDER BY seed,id',(eid,)).fetchall()
        require(len(entries)>=2*e['group_count'],'每组至少需要两人，请增加报名人数或减少小组数')
        require(e['qualifiers']<=len(entries)//e['group_count'],'每组晋级人数不能超过最小小组人数')
        seeded=sorted([p for p in entries if p['seed']],key=lambda p:p['seed']);unseeded=[p for p in entries if not p['seed']];random.SystemRandom().shuffle(unseeded)
        groups=[[] for _ in range(e['group_count'])]
        for i,p in enumerate(seeded+unseeded):
            block,pos=divmod(i,e['group_count']);g=pos if block%2==0 else e['group_count']-1-pos
            groups[g].append(p['user_id']);c.execute('UPDATE event_entries SET group_no=? WHERE id=?',(g,p['id']))
        for g,ids in enumerate(groups):
            for a,bid in itertools.combinations(ids,2):fixture(c,e,a,bid,'group',group=g)
        c.execute("UPDATE events SET status='groups' WHERE id=?",(eid,))
    elif op=='advance':
        require(e['status']=='groups','当前不在小组赛阶段',409)
        unfinished=c.execute("SELECT count(*) FROM event_matches x JOIN matches m ON m.id=x.match_id WHERE x.event_id=? AND x.phase='group' AND m.status!='completed'",(eid,)).fetchone()[0]
        require(not unfinished,'小组赛全部确认赛果后才能生成淘汰赛',409)
        q=b.get('qualifiers',e['qualifiers']);require(type(q)==int and 1<=q<=8,'晋级人数应为1–8')
        groups=[standings(c,eid,g) for g in range(e['group_count'])]
        require(all(len(g)>=q for g in groups),'晋级人数超过某组人数')
        qualifiers=[g[place]['user_id'] for place in range(q) for g in groups]
        require(len(qualifiers)>=2,'淘汰赛至少需要两人')
        size=2
        while size<len(qualifiers):size*=2
        order=[1,2]
        while len(order)<size:order=[v for seed in order for v in (seed,2*len(order)+1-seed)]
        rounds=size.bit_length()-1
        for rnd in range(1,rounds+1):
            for slot in range(size//(2**rnd)):c.execute('INSERT INTO event_bracket(event_id,round_no,slot) VALUES(?,?,?)',(eid,rnd,slot))
        for slot in range(size//2):
            ids=[qualifiers[seed-1] if seed<=len(qualifiers) else None for seed in order[2*slot:2*slot+2]]
            a,bid=ids;mid=fixture(c,e,a,bid,'knockout',round_no=1,slot=slot) if a and bid else None
            winner=(a or bid) if not mid else None
            c.execute('UPDATE event_bracket SET a_id=?,b_id=?,match_id=?,winner_id=? WHERE event_id=? AND round_no=1 AND slot=?',(a,bid,mid,winner,eid,slot))
        c.execute("UPDATE events SET status='knockout',qualifiers=? WHERE id=?",(q,eid));progress(c,eid)
    elif op=='cancel':
        require(e['status']!='cancelled','赛事已取消',409)
        reason=text(b.get('reason'),'取消原因',200,4)
        c.execute("UPDATE matches SET status=CASE WHEN status='completed' THEN 'voided' ELSE 'cancelled' END,void_reason=? WHERE id IN (SELECT match_id FROM event_matches WHERE event_id=?)",(reason,eid))
        c.execute("UPDATE events SET status='cancelled',cancel_reason=? WHERE id=?",(reason,eid));ratings.request_rebuild(c)
    else:raise AppError('未知赛事操作')
    audit(c,u['id'],'event.'+op,eid)
    return {'message':{'draw':'分组与小组循环赛已生成','advance':'淘汰赛已生成，胜者将自动晋级下一轮','cancel':'赛事已取消，相关赛果已作废，积分将在00:00统一重算','seeds':'种子设置已保存'}.get(op,'已保存')}
