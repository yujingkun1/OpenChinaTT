import json
import re
import secrets
import sqlite3
import time
from app import accounts, config, db, matches, ratings, avatars, doubles, statistics, events
from app.security import *

MATCH_SELECT = '''SELECT m.*,(SELECT event_id FROM event_matches em WHERE em.match_id=m.id) event_id,a.name a_name,b.name b_name,a.avatar_version a_avatar_version,b.avatar_version b_avatar_version FROM matches m JOIN users a ON a.id=m.a_id JOIN users b ON b.id=m.b_id'''

def rows(c,sql,args=()):
    return [dict(r) for r in c.execute(sql,args).fetchall()]

def handle(method,path,body,user,session,query):
    u = user
    with db.transaction() as c:
        if u and method!='GET' and path not in ['/auth/register','/auth/login','/auth/send-code','/auth/code-login','/auth/recover']:
            current=c.execute('SELECT * FROM users WHERE id=? AND active=1',(u['id'],)).fetchone()
            valid=c.execute('SELECT user_id FROM sessions WHERE token=? AND expires>?',(session['token'],time.time())).fetchone() if session else None
            require(current and valid and valid['user_id']==u['id'],'会话已失效，请重新登录',401)
            u=current
        if method=='GET':
            if path in ['/health','/ready']:
                c.execute('SELECT 1').fetchone()
                return {'status':'ok','service':'leeds-tt'},None
            if path=='/bootstrap':
                total = c.execute('SELECT count(*) FROM users WHERE active=1').fetchone()[0]
                count = c.execute("SELECT count(*) FROM matches WHERE status='completed'").fetchone()[0]
                double_count = c.execute("SELECT count(*) FROM doubles_matches WHERE status='completed'").fetchone()[0]
                mine = rows(c,MATCH_SELECT+' WHERE (a_id=? OR b_id=?) ORDER BY m.id DESC LIMIT 100',(u['id'],u['id'])) if u else []
                return {'user':accounts.private(u) if u else None,'csrf':session['csrf'] if session else None,'mail_ready':config.MAIL_READY,'club':{'name':config.CLUB_NAME,'full_name':config.CLUB_FULL_NAME,'english_name':config.CLUB_ENGLISH,'admin_name':c.execute("SELECT name FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()[0] if c.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone() else config.ADMIN_NAME},'stats':{'members':total,'matches':count+double_count,'singles':count,'doubles':double_count},'mine':mine,'mine_doubles':doubles.list_matches(c,u['id']) if u else [],'admin':admin_data(c) if u and u['role']=='admin' else None},None
            if path=='/events':
                return {'events':events.list_events(c)},None
            if re.fullmatch(r'/events/\d+',path):
                return events.public(c,int(path.rsplit('/',1)[1])),None
            if path=='/analytics':
                return statistics.collect(c),None
            if path=='/doubles':
                return {'doubles':doubles.list_matches(c)},None
            if path=='/players':
                players = rows(c,'''SELECT '''+','.join('u.'+k for k in accounts.PUBLIC.split(','))+''',
                (SELECT count(*) FROM matches WHERE status='completed' AND (a_id=u.id OR b_id=u.id)) played,
                (SELECT count(*) FROM matches WHERE status='completed' AND winner_id=u.id) wins
                FROM users u WHERE active=1 ORDER BY rating DESC,u.id''')
                return {'players':players},None
            if re.fullmatch(r'/players/\d+',path):
                pid = int(path.rsplit('/',1)[1])
                p = c.execute('SELECT * FROM users WHERE id=?',(pid,)).fetchone()
                require(p,'球友不存在',404)
                history = rows(c,MATCH_SELECT+" WHERE status IN ('completed','voided') AND (a_id=? OR b_id=?) ORDER BY confirmed_at DESC,m.id DESC LIMIT 100",(pid,pid))
                logs = rows(c,'SELECT l.*,m.confirmed_at AS effective_at FROM rating_log l JOIN matches m ON m.id=l.match_id WHERE l.user_id=? ORDER BY l.id',(pid,))
                inactivity = rows(c,'SELECT * FROM inactivity_log WHERE user_id=? ORDER BY due_at',(pid,))
                return dict(player=accounts.public(p),matches=history,history=logs,inactivity=inactivity),None
            if path=='/matches':
                result = rows(c,MATCH_SELECT+" WHERE status IN ('completed','voided') ORDER BY confirmed_at DESC,m.id DESC LIMIT 200")
                return {'matches':result},None
            if path=='/rules':
                return {'bands':[[upper if upper!=float('inf') else None,x,y] for upper,x,y in ratings.BANDS],'source':ratings.SOURCE},None
            raise AppError('页面不存在',404)
        if path=='/auth/register':
            return accounts.register(c,body)
        if path=='/auth/login':
            return accounts.login(c,body)
        if path=='/auth/send-code':
            return accounts.send_code(c,body),None
        if path=='/auth/code-login':
            return accounts.code_login(c,body)
        if path=='/auth/recover':
            mail = email(body.get('email'))
            recovery = text(body.get('recovery_code'),'恢复码',100,10)
            p = password(body.get('password'))
            account = c.execute('SELECT * FROM users WHERE email=?',(mail,)).fetchone()
            require(account and account['active'] and secrets.compare_digest(account['recovery_hash'],digest(recovery)),'邮箱或恢复码错误',401)
            replacement = secrets.token_urlsafe(24)
            c.execute('UPDATE users SET password=?,recovery_hash=? WHERE id=?',(hash_password(p),digest(replacement),account['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?',(account['id'],))
            return {'message':'密码已更新，请登录并保存新恢复码','recovery_code':replacement},None
        require(u,'请先登录',401)
        if path=='/auth/logout':
            c.execute('DELETE FROM sessions WHERE token=?',(session['token'],))
            return {'message':'已退出登录'},'clear'
        if path=='/events':
            require(u['role']=='admin','需要管理员权限',403)
            return events.create(c,u,body),None
        if re.fullmatch(r'/events/\d+/[a-z]+',path):
            parts=path.split('/')
            return events.action(c,u,int(parts[2]),parts[3],body),None
        if path=='/profile/title':
            title=text(body.get('title',''),'称号',10)
            c.execute('UPDATE users SET title=? WHERE id=?',(title,u['id']))
            matches.audit(c,u['id'],'profile.title',u['id'])
            return {'message':'称号已保存','title':title},None
        if path=='/profile/avatar':
            return avatars.save(c,u['id'],body),None
        if path=='/profile':
            return accounts.profile(c,u,body),None
        if path=='/auth/password':
            require(check_password(body.get('old_password',''),u['password']),'当前密码错误',403)
            p = password(body.get('password'))
            c.execute('UPDATE users SET password=? WHERE id=?',(hash_password(p),u['id']))
            c.execute('DELETE FROM sessions WHERE user_id=? AND token!=?',(u['id'],session['token']))
            return {'message':'密码已更改，其他设备已退出'},None
        if path=='/doubles':
            return doubles.create(c,u,body),None
        if re.fullmatch(r'/doubles/\d+',path):
            return doubles.action(c,u,int(path.rsplit('/',1)[1]),body),None
        if path=='/matches':
            return matches.create(c,u,body),None
        if re.fullmatch(r'/matches/\d+',path):
            return matches.action(c,u,int(path.rsplit('/',1)[1]),body),None
        require(u['role']=='admin','需要管理员权限',403)
        if path=='/admin/doubles-void':
            reason=text(body.get('reason'),'作废原因',200,4)
            mid=body.get('match_id')
            require(type(mid)==int,'请选择双打比赛')
            m=c.execute('SELECT * FROM doubles_matches WHERE id=?',(mid,)).fetchone()
            require(m and m['status']=='completed','只能作废已完成的双打',409)
            c.execute("UPDATE doubles_matches SET status='voided',void_reason=? WHERE id=?",(reason,mid))
            matches.audit(c,u['id'],'admin.doubles-void',f'{mid}: {reason}')
            return {'message':'双打已作废，组合战绩已更新；个人积分不变'},None
        if path=='/admin/void':
            reason = text(body.get('reason'),'作废原因',200,4)
            mid = body.get('match_id')
            require(type(mid)==int,'请选择比赛')
            m = c.execute('SELECT * FROM matches WHERE id=?',(mid,)).fetchone()
            require(m and m['status']=='completed','只能作废已结算的比赛',409)
            events.guard_void(c,mid)
            c.execute("UPDATE matches SET status='voided',void_reason=? WHERE id=?",(reason,mid))
            ratings.request_rebuild(c)
            matches.audit(c,u['id'],'admin.void',f'{mid}: {reason}')
            return {'message':'比赛已作废，积分将在北京时间00:00统一重算'},None
        if path=='/admin/user':
            pid = body.get('user_id')
            require(type(pid)==int,'请选择用户')
            target = c.execute('SELECT * FROM users WHERE id=?',(pid,)).fetchone()
            require(target and target['role']!='admin','不能修改管理员账号',403)
            active = body.get('active')
            require(type(active)==bool,'请选择用户状态')
            c.execute('UPDATE users SET active=? WHERE id=?',(int(active),pid))
            if not active:
                c.execute('DELETE FROM sessions WHERE user_id=?',(pid,))
                c.execute("UPDATE matches SET status='cancelled' WHERE (a_id=? OR b_id=?) AND status IN ('invited','accepted','reported')",(pid,pid))
                c.execute("UPDATE doubles_matches SET status='cancelled' WHERE ? IN (a1,a2,b1,b2) AND status IN ('invited','accepted','reported')",(pid,))
            matches.audit(c,u['id'],'admin.user',f'{pid}: active={active}')
            return {'message':'用户状态已更新'},None
        raise AppError('接口不存在',404)

def admin_data(c):
    return {'users':rows(c,'SELECT id,name,email,role,active,created_at FROM users ORDER BY id'),
            'audit':rows(c,'SELECT a.*,u.name actor_name FROM audit a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 50')}
