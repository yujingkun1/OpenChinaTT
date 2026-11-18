import json
from datetime import datetime, timezone
from app.security import *
from app import ratings

def audit(c,uid,action,target):
    c.execute('INSERT INTO audit(actor_id,action,target) VALUES(?,?,?)',(uid,action,str(target)))

def create(c,u,b):
    opponent = b.get('opponent_id')
    require(type(opponent)==int and opponent!=u['id'],'请选择另一位球友')
    require(c.execute('SELECT id FROM users WHERE id=? AND active=1',(opponent,)).fetchone(),'球友不存在或已停用',404)
    best = b.get('best_of',5)
    require(type(best)==int and best in [3,5,7],'赛制需为三、五或七局两胜/三胜/四胜')
    venue = text(b.get('venue'), '地点',100,2)
    scheduled = text(b.get('scheduled_at'), '时间',40,10)
    try:
        dt = datetime.fromisoformat(scheduled.replace('Z','+00:00'))
        require(dt.tzinfo is not None, '时间必须包含时区')
        scheduled = dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise AppError('比赛时间格式不正确')
    note = text(b.get('note',''),'备注',200)
    mid = c.execute('INSERT INTO matches(a_id,b_id,best_of,venue,scheduled_at,note) VALUES(?,?,?,?,?,?)',(u['id'],opponent,best,venue,scheduled,note)).lastrowid
    audit(c,u['id'],'match.invite',mid)
    return {'id':mid,'message':'邀请已发出，等待对方同意'}

def action(c,u,mid,b):
    m = c.execute('SELECT * FROM matches WHERE id=?',(mid,)).fetchone()
    require(m,'对战不存在',404)
    require(u['id'] in [m['a_id'],m['b_id']],'只有对战双方可操作',403)
    act = b.get('action')
    if act in ['accept','decline']:
        require(m['status']=='invited','邀请状态已改变，请刷新',409)
        require(u['id']==m['b_id'],'只有受邀球友可回应',403)
        c.execute('UPDATE matches SET status=? WHERE id=?',('accepted' if act=='accept' else 'declined',mid))
    elif act=='cancel':
        require(not c.execute('SELECT match_id FROM event_matches WHERE match_id=?',(mid,)).fetchone(),'赛事对战不能单独取消，请联系管理员处理赛事',409)
        require(m['status'] in ['invited','accepted'],'当前对战不能取消',409)
        c.execute("UPDATE matches SET status='cancelled' WHERE id=?",(mid,))
    elif act=='report':
        require(m['status']=='accepted','需双方同意对战后再提交赛果',409)
        games = b.get('games')
        a,bw = validate_games(games,m['best_of'])
        winner = m['a_id'] if a>bw else m['b_id']
        c.execute("UPDATE matches SET status='reported',score_a=?,score_b=?,games=?,reporter_id=?,winner_id=?,reported_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",(a,bw,json.dumps(games),u['id'],winner,mid))
    elif act in ['confirm','reject']:
        require(m['status']=='reported','赛果状态已改变，请刷新',409)
        require(u['id']!=m['reporter_id'],'请等待另一方确认赛果',403)
        if act=='confirm':
            c.execute("UPDATE matches SET status='completed',confirmed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",(mid,))
            from app.events import match_changed
            match_changed(c,mid)
        else:
            c.execute("UPDATE matches SET status='accepted',score_a=NULL,score_b=NULL,games=NULL,reporter_id=NULL,winner_id=NULL,reported_at=NULL WHERE id=?",(mid,))
    else:
        raise AppError('未知操作')
    audit(c,u['id'],'match.'+act,mid)
    if act=='report' and c.execute('SELECT 1 FROM event_matches WHERE match_id=?',(mid,)).fetchone():
        return {'message':'赛事成绩已录入，比分表已更新，未确认成绩将在00:00自动确认并结算'}
    return {'message':{'confirm':'赛果已确认，积分将在北京时间00:00统一结算','reject':'已退回赛果，请重新协商','accept':'已接受邀请，祝比赛愉快'}.get(act,'操作成功')}

def validate_games(games,best_of):
    require(isinstance(games,list) and 1<=len(games)<=best_of,'请填写每局比分')
    a,bw = 0,0
    target = best_of//2+1
    for game in games:
        require(isinstance(game,list) and len(game)==2 and all(type(x)==int and 0<=x<=199 for x in game),'单局比分必须为0–199的整数')
        x,y = game
        require(max(x,y)>=11 and ((max(x,y)==11 and min(x,y)<=9) or (min(x,y)>=10 and abs(x-y)==2)),'单局需11分获胜，10平后领先2分获胜')
        require(a<target and bw<target,'比赛已分出胜负，不应再有后续局')
        a += x>y
        bw += y>x
    require(max(a,bw)==target,'完整赛果需达到赛制规定的胜局数')
    return a,bw
