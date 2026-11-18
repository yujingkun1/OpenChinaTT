import itertools
import json
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

def registration_open(event):
    return event['status'] == 'open' and (
        event['qualifiers'] != 0 or datetime.now(timezone.utc) < datetime.fromisoformat(event['starts_at'])
    )

def public(c,eid):
    e=dict(get(c,eid))
    e['registration_open'] = registration_open(e)
    e['entries']=[dict(r) for r in c.execute('SELECT e.*,u.name,u.avatar_version,u.rating FROM event_entries e JOIN users u ON u.id=e.user_id WHERE event_id=? ORDER BY coalesce(seed,999999),e.id',(eid,))]
    e['matches']=[dict(r) for r in c.execute('''SELECT m.*,a.name a_name,b.name b_name,a.avatar_version a_avatar_version,b.avatar_version b_avatar_version,x.event_id,x.phase,x.group_no,x.round_no,x.slot FROM event_matches x JOIN matches m ON m.id=x.match_id JOIN users a ON a.id=m.a_id JOIN users b ON b.id=m.b_id WHERE x.event_id=? ORDER BY x.phase,x.group_no,x.round_no,x.slot,m.id''',(eid,))]
    e['groups']=[{'number':i,'standings':standings(c,eid,i)} for i in range(e['group_count'])] if any(p['group_no'] is not None for p in e['entries']) else []
    e['bracket']=[dict(r) for r in c.execute('SELECT * FROM event_bracket WHERE event_id=? ORDER BY round_no,slot',(eid,))]
    return e

def list_events(c):
    rows = c.execute('''SELECT e.*,(SELECT count(*) FROM event_entries x WHERE x.event_id=e.id) participants,u.name champion_name FROM events e LEFT JOIN users u ON u.id=e.champion_id ORDER BY e.id DESC LIMIT 100''')
    return [dict(r, registration_open=registration_open(r)) for r in rows]

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
    require(type(q)==int and 0<=q<=8,'每组晋级人数应为0–8')
    require(q!=0 or groups==1,'大循环赛事须设为一组')
    require(type(best)==int and best in [3,5,7],'请选择有效赛制')
    ceiling = b.get('rating_ceiling')
    require(ceiling is None or (type(ceiling)==int and 1<=ceiling<=9999),'报名积分上限应为1–9999')
    eid=c.execute('INSERT INTO events(title,kind,venue,starts_at,best_of,group_count,qualifiers,description,created_by,rating_ceiling) VALUES(?,?,?,?,?,?,?,?,?,?)',(title,kind,venue,date,best,groups,q,text(b.get('description',''),'赛事说明',500),u['id'],ceiling)).lastrowid
    audit(c,u['id'],'event.create',eid)
    return {'id':eid,'message':'赛事已创建，球友现在可以报名'}

def fixture(c,e,a,b,phase,group=None,round_no=None,slot=None):
    label = '大循环' if e['qualifiers'] == 0 else {'group':'小组赛','knockout':'淘汰赛'}[phase]
    mid=c.execute("INSERT INTO matches(a_id,b_id,status,best_of,venue,scheduled_at,note) VALUES(?,?,'accepted',?,?,?,?)",(a,b,e['best_of'],e['venue'],e['starts_at'],f"赛事：{e['title']} · {label}")).lastrowid
    c.execute('INSERT INTO event_matches VALUES(?,?,?,?,?,?)',(e['id'],mid,phase,group,round_no,slot))
    return mid

def standings(c,eid,group):
    entrants=[dict(r) for r in c.execute('SELECT e.user_id,e.seed,e.id entry_id,e.absent_reason,u.name,u.avatar_version FROM event_entries e JOIN users u ON u.id=e.user_id WHERE event_id=? AND group_no=?',(eid,group))]
    rows={p['user_id']:dict(p,played=0,wins=0,game_diff=0,point_diff=0,tie_wins=0) for p in entrants}
    ms=c.execute("SELECT m.* FROM event_matches x JOIN matches m ON m.id=x.match_id WHERE x.event_id=? AND x.group_no=? AND x.phase='group' AND m.status IN ('completed','reported')",(eid,group)).fetchall()
    import json
    for m in ms:
        games=json.loads(m['games'])
        pd=sum(g[0]-g[1] for g in games)
        for uid,sg,pdiff in [(m['a_id'],m['score_a']-m['score_b'],pd),(m['b_id'],m['score_b']-m['score_a'],-pd)]:
            r=rows[uid];r['played']+=1;r['wins']+=int(m['winner_id']==uid);r['game_diff']+=sg;r['point_diff']+=pdiff
    for m in ms:
        if m['winner_id'] and rows[m['a_id']]['wins']==rows[m['b_id']]['wins']:rows[m['winner_id']]['tie_wins']+=1
    return sorted(rows.values(),key=lambda r:(bool(r['absent_reason']),-r['wins'],-r['tie_wins'],-r['game_diff'],-r['point_diff'],r['seed'] or 999999,r['entry_id']))

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
    if not linked:
        return
    eid = linked['event_id']
    event = get(c,eid)
    if event['qualifiers'] == 0 and event['status'] == 'groups':
        unfinished = c.execute("SELECT 1 FROM event_matches x JOIN matches m ON m.id=x.match_id WHERE x.event_id=? AND m.status!='completed' LIMIT 1",(eid,)).fetchone()
        if not unfinished:
            eligible = [p for p in standings(c,eid,0) if not p['absent_reason']]
            champion = eligible[0]['user_id'] if eligible else None
            c.execute("UPDATE events SET status='completed',champion_id=? WHERE id=?",(champion,eid))
        return
    progress(c,eid)

def start_round_robin(c,eid):
    event = get(c,eid)
    require(event['qualifiers']==0,'只能定时开启大循环赛事',409)
    if event['status']!='open':
        return {'status':event['status']}
    require(not registration_open(event),'尚未到赛事开赛时间',409)
    actor = c.execute("SELECT * FROM users WHERE id=? AND role='admin' AND active=1",(event['created_by'],)).fetchone()
    require(actor,'赛事创建者需要有效的管理员权限',403)
    count = c.execute('SELECT count(*) FROM event_entries e JOIN users u ON u.id=e.user_id WHERE e.event_id=? AND u.active=1',(eid,)).fetchone()[0]
    if count < 2:
        return {'status':'cancelled',**action(c,actor,eid,'cancel',{'reason':'开赛时有效报名人数不足两人'})}
    return {'status':'groups',**action(c,actor,eid,'draw',{})}

def guard_void(c,mid):
    linked=c.execute('SELECT x.phase,e.status,e.qualifiers FROM event_matches x JOIN events e ON e.id=x.event_id WHERE x.match_id=?',(mid,)).fetchone()
    if linked:require(linked['qualifiers']!=0 and linked['phase']=='group' and linked['status']=='groups','此赛事赛果不能单独作废；可在赛事管理中取消整场赛事',409)

def correct_result(c,u,e,body):
    from app.matches import validate_games
    mid=body.get('match_id')
    require(type(mid)==int,'请选择赛事对战')
    match=c.execute('SELECT m.*,x.phase,x.round_no,x.slot FROM matches m JOIN event_matches x ON x.match_id=m.id WHERE x.event_id=? AND m.id=?',(e['id'],mid)).fetchone()
    require(match and match['status'] in ['accepted','reported','completed'],'只能修改有效的赛事对战',409)
    reason=text(body.get('reason'),'修改原因',200,2)
    games=body.get('games')
    a,b=validate_games(games,match['best_of'])
    winner=match['a_id'] if a>b else match['b_id']
    changed=match['winner_id']!=winner
    eid=e['id']
    if (changed or games!=json.loads(match['games'] or '[]')) and match['phase']=='group' and c.execute('SELECT 1 FROM event_bracket WHERE event_id=?',(eid,)).fetchone():
        # Archive dependent results, then let the administrator draw qualifiers
        # again from the corrected group standings. Original scores stay stored.
        c.execute("UPDATE matches SET status=CASE WHEN status='completed' THEN 'voided' ELSE 'cancelled' END,void_reason=? WHERE id IN (SELECT match_id FROM event_matches WHERE event_id=? AND phase='knockout')",('小组赛果更正，原淘汰赛对阵失效：'+reason,eid))
        c.execute('DELETE FROM event_bracket WHERE event_id=?',(eid,))
        c.execute("UPDATE events SET status='groups',champion_id=NULL WHERE id=?",(eid,))
        ratings.request_rebuild(c)
    elif changed and match['phase']=='knockout':
        rnd,slot=match['round_no'],match['slot']
        while True:
            rnd,slot=rnd+1,slot//2
            row=c.execute('SELECT * FROM event_bracket WHERE event_id=? AND round_no=? AND slot=?',(eid,rnd,slot)).fetchone()
            if not row:
                break
            if row['match_id']:
                c.execute("UPDATE matches SET status=CASE WHEN status='completed' THEN 'voided' ELSE 'cancelled' END,void_reason=? WHERE id=?",('前轮赛果更正，原对阵失效：'+reason,row['match_id']))
            c.execute('UPDATE event_bracket SET a_id=NULL,b_id=NULL,winner_id=NULL,match_id=NULL WHERE event_id=? AND round_no=? AND slot=?',(eid,rnd,slot))
        c.execute('UPDATE event_bracket SET winner_id=? WHERE event_id=? AND match_id=?',(winner,eid,mid))
        c.execute("UPDATE events SET status='knockout',champion_id=NULL WHERE id=?",(eid,))
        ratings.request_rebuild(c)
    if e['qualifiers']==0:
        c.execute("UPDATE events SET status='groups',champion_id=NULL WHERE id=?",(eid,))
    c.execute("UPDATE matches SET status='completed',score_a=?,score_b=?,games=?,winner_id=?,reporter_id=?,forfeit_reason='',confirmed_at=coalesce(confirmed_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')),reported_at=coalesce(reported_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id=?",(a,b,json.dumps(games),winner,u['id'],mid))
    if match['settled_at']:
        ratings.request_rebuild(c)
    audit(c,u['id'],'event.result-correct',json.dumps({'event_id':eid,'match_id':mid,'reason':reason,'before':{'score_a':match['score_a'],'score_b':match['score_b'],'games':match['games'],'winner_id':match['winner_id']},'after':{'games':games,'winner_id':winner}},ensure_ascii=False))
    match_changed(c,mid)
    return {'message':'成绩已修改，比分表已更新，个人积分将在00:00结算或重算'}

def action(c,u,eid,op,b):
    e=get(c,eid)
    if op in ['join','leave']:
        require(registration_open(e),'赛事报名已结束',409)
        if op=='join':
            ceiling=e['rating_ceiling']
            require(ceiling is None or u['rating']<ceiling,f'报名时个人积分须低于{ceiling}分',409)
            require(c.execute('SELECT count(*) FROM event_entries WHERE event_id=?',(eid,)).fetchone()[0]<128,'赛事人数已满')
            require(not c.execute('SELECT id FROM event_entries WHERE event_id=? AND user_id=?',(eid,u['id'])).fetchone(),'你已经报名',409)
            c.execute('INSERT INTO event_entries(event_id,user_id) VALUES(?,?)',(eid,u['id']))
        else:c.execute('DELETE FROM event_entries WHERE event_id=? AND user_id=?',(eid,u['id']))
        audit(c,u['id'],'event.'+op,eid)
        return {'message':'已报名，赛程生成后可查看对阵' if op=='join' else '已退出报名'}
    require(u['role']=='admin','需要管理员权限',403)
    if op=='result':
        require(e['status']!='cancelled','赛事已取消',409)
        return correct_result(c,u,e,b)
    if op=='absent':
        require(e['status']=='groups','只能在循环赛进行中标记缺席',409)
        require(datetime.now(timezone.utc)>=datetime.fromisoformat(e['starts_at']),'开赛后才能标记缺席',409)
        ids=b.get('user_ids')
        require(isinstance(ids,list) and 1<=len(ids)<=128 and all(type(uid)==int for uid in ids),'请选择缺席球员')
        reason=text(b.get('reason'),'缺席原因',200,2)
        for uid in set(ids):
            require(c.execute('SELECT 1 FROM event_entries WHERE event_id=? AND user_id=?',(eid,uid)).fetchone(),'只能标记已报名的球员')
            c.execute('UPDATE event_entries SET absent_reason=? WHERE event_id=? AND user_id=?',(reason,eid,uid))
        absent={r['user_id'] for r in c.execute("SELECT user_id FROM event_entries WHERE event_id=? AND absent_reason!=''",(eid,))}
        fixtures=c.execute("SELECT m.* FROM matches m JOIN event_matches x ON x.match_id=m.id WHERE x.event_id=? AND x.phase='group' AND (m.status='accepted' OR (m.status='completed' AND m.forfeit_reason!='')) ORDER BY m.id",(eid,)).fetchall()
        changed=[]
        for m in fixtures:
            a_absent=m['a_id'] in absent
            b_absent=m['b_id'] in absent
            if not (a_absent or b_absent):
                continue
            winner=None if a_absent and b_absent else (m['b_id'] if a_absent else m['a_id'])
            if m['forfeit_reason'] and m['winner_id']==winner:
                continue
            target=m['best_of']//2+1
            score_a=target if winner==m['a_id'] else 0
            score_b=target if winner==m['b_id'] else 0
            label='双方缺席，双方判负' if winner is None else '管理员标记缺席，弃权判负'
            # No played rallies are invented for an administrative forfeit.
            c.execute("UPDATE matches SET status='completed',score_a=?,score_b=?,games='[]',winner_id=?,reporter_id=NULL,forfeit_reason=?,confirmed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",(score_a,score_b,winner,label,m['id']))
            if m['settled_at']:
                ratings.request_rebuild(c)
            changed.append(m['id'])
        if changed:
            match_changed(c,changed[-1])
        audit(c,u['id'],'event.absent',json.dumps({'event_id':eid,'user_ids':sorted(set(ids)),'reason':reason,'match_ids':changed},ensure_ascii=False))
        return {'message':f'缺席已标记，{len(changed)}场对战已判负，积分将在00:00结算','affected_matches':len(changed)}
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
        require(e['qualifiers']!=0 or not registration_open(e),'尚未到赛事开赛时间',409)
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
        require(e['qualifiers']!=0,'大循环赛事按循环赛排名完赛',409)
        require(e['status']=='groups','当前不在小组赛阶段',409)
        unfinished=c.execute("SELECT count(*) FROM event_matches x JOIN matches m ON m.id=x.match_id WHERE x.event_id=? AND x.phase='group' AND m.status!='completed'",(eid,)).fetchone()[0]
        require(not unfinished,'小组赛全部确认赛果后才能生成淘汰赛',409)
        q=b.get('qualifiers',e['qualifiers']);require(type(q)==int and 1<=q<=8,'晋级人数应为1–8')
        groups=[[p for p in standings(c,eid,g) if not p['absent_reason']] for g in range(e['group_count'])]
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
        c.execute("UPDATE events SET status='cancelled',cancel_reason=? WHERE id=?",(reason,eid))
        if c.execute('SELECT 1 FROM event_matches x JOIN rating_log l ON l.match_id=x.match_id WHERE x.event_id=? LIMIT 1',(eid,)).fetchone():
            ratings.request_rebuild(c)
    else:raise AppError('未知赛事操作')
    audit(c,u['id'],'event.'+op,eid)
    if op=='draw' and e['qualifiers']==0:
        return {'message':'大循环赛程已生成'}
    return {'message':{'draw':'分组与小组循环赛已生成','advance':'淘汰赛已生成，胜者将自动晋级下一轮','cancel':'赛事已取消，相关赛果已作废，积分将在00:00统一重算','seeds':'种子设置已保存'}.get(op,'已保存')}
