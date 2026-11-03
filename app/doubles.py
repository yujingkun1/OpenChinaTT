import json
from datetime import datetime,timezone
from app.security import *
from app.matches import audit

SELECT='''SELECT d.*,a.name a1_name,b.name a2_name,x.name b1_name,y.name b2_name,
 a.avatar_version a1_avatar_version,b.avatar_version a2_avatar_version,x.avatar_version b1_avatar_version,y.avatar_version b2_avatar_version
 FROM doubles_matches d JOIN users a ON a.id=d.a1 JOIN users b ON b.id=d.a2 JOIN users x ON x.id=d.b1 JOIN users y ON y.id=d.b2'''

def list_matches(c,user_id=None):
    where=" WHERE d.status IN ('completed','voided')" if user_id is None else ' WHERE ? IN (d.a1,d.a2,d.b1,d.b2)'
    args=() if user_id is None else (user_id,)
    out=[]
    for row in c.execute(SELECT+where+' ORDER BY d.id DESC LIMIT 200',args):
        m=dict(row)
        m['consents']=[dict(r) for r in c.execute('SELECT user_id,accepted,confirmed FROM doubles_consents WHERE match_id=?',(m['id'],))]
        out.append(m)
    return out

def create(c,u,b):
    team=[u['id'],b.get('partner_id'),b.get('opponent1_id'),b.get('opponent2_id')]
    require(all(type(x)==int for x in team) and len(set(team))==4,'双打需要四位不同的球友')
    for pid in team:
        require(c.execute('SELECT id FROM users WHERE id=? AND active=1',(pid,)).fetchone(),'球友不存在或已停用',404)
    best=b.get('best_of',5)
    require(type(best)==int and best in [3,5,7],'请选择有效赛制')
    venue=text(b.get('venue'),'地点',100,2)
    date=text(b.get('scheduled_at'),'比赛时间',40,10)
    try:
        dt=datetime.fromisoformat(date.replace('Z','+00:00'))
        require(dt.tzinfo is not None,'时间必须包含时区')
        date=dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise AppError('比赛时间格式不正确')
    note=text(b.get('note',''),'备注',200)
    mid=c.execute('INSERT INTO doubles_matches(a1,a2,b1,b2,best_of,venue,scheduled_at,note) VALUES(?,?,?,?,?,?,?,?)',(*team,best,venue,date,note)).lastrowid
    for pid in team:
        c.execute('INSERT INTO doubles_consents(match_id,user_id,accepted) VALUES(?,?,?)',(mid,pid,int(pid==u['id'])))
    audit(c,u['id'],'doubles.invite',mid)
    return {'id':mid,'message':'双打邀请已发出，等待另外三位球友同意'}

def action(c,u,mid,body):
    m=c.execute('SELECT * FROM doubles_matches WHERE id=?',(mid,)).fetchone()
    require(m,'双打对战不存在',404)
    require(u['id'] in [m['a1'],m['a2'],m['b1'],m['b2']],'只有参赛四人可操作',403)
    act=body.get('action')
    consent=c.execute('SELECT * FROM doubles_consents WHERE match_id=? AND user_id=?',(mid,u['id'])).fetchone()
    message='操作成功'
    if act in ['accept','decline']:
        require(m['status']=='invited','邀请状态已改变，请刷新',409)
        require(u['id']!=m['a1'],'发起者已同意自己的邀请',403)
        if act=='decline':
            c.execute("UPDATE doubles_matches SET status='declined' WHERE id=?",(mid,))
        else:
            require(not consent['accepted'],'你已接受，请等待其余球友',409)
            c.execute('UPDATE doubles_consents SET accepted=1 WHERE match_id=? AND user_id=?',(mid,u['id']))
            count=c.execute('SELECT sum(accepted) FROM doubles_consents WHERE match_id=?',(mid,)).fetchone()[0]
            if count==4:c.execute("UPDATE doubles_matches SET status='accepted' WHERE id=?",(mid,))
            message=f'已同意，当前{count}/4人接受'
    elif act=='cancel':
        require(m['status'] in ['invited','accepted'],'当前对战不能取消',409)
        c.execute("UPDATE doubles_matches SET status='cancelled' WHERE id=?",(mid,))
    elif act=='report':
        require(m['status']=='accepted','四人同意对战后方可提交赛果',409)
        games=body.get('games')
        require(isinstance(games,list) and 1<=len(games)<=m['best_of'],'请填写每局比分')
        a,b=0,0; target=m['best_of']//2+1
        for g in games:
            require(isinstance(g,list) and len(g)==2 and all(type(x)==int and 0<=x<=199 for x in g),'每局比分必须为0–199的整数')
            x,y=g
            require(max(x,y)>=11 and ((max(x,y)==11 and min(x,y)<=9) or (min(x,y)>=10 and abs(x-y)==2)),'每局11分，10平后需领先2分')
            require(a<target and b<target,'比赛已结束，不应录入后续局')
            a+=x>y;b+=y>x
        require(max(a,b)==target,'赛果须达到赛制规定的胜局数')
        c.execute("UPDATE doubles_matches SET status='reported',score_a=?,score_b=?,games=?,reporter_id=? WHERE id=?",(a,b,json.dumps(games),u['id'],mid))
        c.execute('UPDATE doubles_consents SET confirmed=CASE WHEN user_id=? THEN 1 ELSE 0 END WHERE match_id=?',(u['id'],mid))
        message='赛果已提交，等待其余三人确认；双打不变更个人积分'
    elif act in ['confirm','reject']:
        require(m['status']=='reported','赛果状态已改变，请刷新',409)
        require(u['id']!=m['reporter_id'],'提交者已经确认自己的赛果',403)
        if act=='reject':
            c.execute("UPDATE doubles_matches SET status='accepted',score_a=NULL,score_b=NULL,games=NULL,reporter_id=NULL WHERE id=?",(mid,))
            c.execute('UPDATE doubles_consents SET confirmed=0 WHERE match_id=?',(mid,))
            message='赛果已退回，请四人核对后重新提交'
        else:
            require(not consent['confirmed'],'你已确认，请等待其余球友',409)
            c.execute('UPDATE doubles_consents SET confirmed=1 WHERE match_id=? AND user_id=?',(mid,u['id']))
            count=c.execute('SELECT sum(confirmed) FROM doubles_consents WHERE match_id=?',(mid,)).fetchone()[0]
            if count==4:
                c.execute("UPDATE doubles_matches SET status='completed',confirmed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",(mid,))
                message='四人已确认，双打组合战绩已更新，个人积分不变'
            else:message=f'已确认，当前{count}/4人确认赛果'
    else:
        raise AppError('未知操作')
    audit(c,u['id'],'doubles.'+act,mid)
    return {'message':message}
