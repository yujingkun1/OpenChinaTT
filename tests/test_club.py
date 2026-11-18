import io
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
from app import api,config,db,events,ratings
from app.security import hash_password,digest,AppError
from app.server import application

class ClubTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        config.DATA=Path(self.temp.name)
        config.DB=config.DATA/'test.sqlite3'
        db.init()
        with db.transaction() as c:
            for i in range(1,7):
                c.execute('INSERT INTO users(id,email,name,password,role,recovery_hash) VALUES(?,?,?,?,?,?)',(i,f'p{i}@example.com',f'球友{i}',hash_password('correct-password'), 'admin' if i==1 else 'user',digest('recovery-code-'+str(i))))
                c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(f'fake-{i}',i,'fake',time.time()+86400))
        self.users={}
        with db.connect() as c:
            self.users={r['id']:dict(r) for r in c.execute('SELECT * FROM users')}
    def tearDown(self):
        self.temp.cleanup()
    def call(self,path,data=None,uid=2):
        return api.handle('POST' if data is not None else 'GET',path,data or {},self.users.get(uid),{'token':f'fake-{uid}','csrf':'fake'}, {})[0]
    def match(self,a=2,b=3,best=3):
        date=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        mid=self.call('/matches',{'opponent_id':b,'best_of':best,'venue':'测试球馆','scheduled_at':date},a)['id']
        self.call('/matches/'+str(mid),{'action':'accept'},b)
        with db.transaction() as c:
            c.execute('UPDATE matches SET scheduled_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),mid))
        return mid
    def report(self,mid,uid=2,games=None):
        return self.call('/matches/'+str(mid),{'action':'report','games':games or [[11,5],[11,7]]},uid)
    def settle(self):
        with db.transaction() as c:
            c.execute("UPDATE matches SET confirmed_at=? WHERE status='completed' AND settled_at IS NULL",((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(timespec='milliseconds').replace('+00:00','Z'),))
            c.execute("UPDATE settings SET value=? WHERE key='rating_rebuild_pending'",((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(timespec='milliseconds').replace('+00:00','Z'),))
            return ratings.settle_due(c)
    def rating(self,uid):
        with db.connect() as c:
            return c.execute('SELECT rating FROM users WHERE id=?',(uid,)).fetchone()[0]
    def test_official_examples_and_boundaries(self):
        self.assertEqual(ratings.exchange(1500,1500),8)
        self.assertEqual(ratings.exchange(2000,1950),6)
        self.assertEqual(ratings.exchange(1950,2000),13)
        self.assertEqual(ratings.exchange(1512,1500),8)
        self.assertEqual(ratings.exchange(1513,1500),7)
        self.assertEqual(ratings.exchange(1737,1500),1)
        self.assertEqual(ratings.exchange(1738,1500),0)
        self.assertEqual(ratings.exchange(1500,1737),45)
        self.assertEqual(ratings.exchange(1500,1738),50)
    def test_registration_starts_1500_cannot_escalate(self):
        data={'name':'新球友','email':'new@example.com','password':'new-password-123','rating':9999,'role':'admin'}
        r=self.call('/auth/register',data,None)
        self.assertEqual(r['user']['rating'],1500)
        self.assertEqual(r['user']['role'],'user')
        self.assertTrue(r['recovery_code'])
    def test_admin_identity_reserved(self):
        for name,email in [(config.ADMIN_NAME,'other@example.com'),('别人',config.ADMIN_EMAIL)]:
            with self.assertRaises(AppError):
                self.call('/auth/register',{'name':name,'email':email,'password':'new-password-123'},None)
    def test_accept_only_opponent(self):
        date=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        mid=self.call('/matches',{'opponent_id':3,'best_of':3,'venue':'测试球馆','scheduled_at':date})['id']
        with self.assertRaises(AppError):self.call('/matches/'+str(mid),{'action':'accept'})
        with self.assertRaises(AppError):self.report(mid)
    def test_no_rating_until_opponent_confirmation(self):
        mid=self.match(); self.report(mid)
        self.assertEqual(self.rating(2),1500)
        with self.assertRaises(AppError):self.call('/matches/'+str(mid),{'action':'confirm'},2)
        self.call('/matches/'+str(mid),{'action':'confirm'},3)
        self.settle()
        self.assertEqual((self.rating(2),self.rating(3)),(1508,1492))
        with self.assertRaises(AppError):self.call('/matches/'+str(mid),{'action':'confirm'},3)
        self.assertEqual(self.rating(2),1508)
    def test_confirmation_concurrent_is_once(self):
        mid=self.match();self.report(mid)
        def confirm(_):
            try:self.call('/matches/'+str(mid),{'action':'confirm'},3);return True
            except AppError:return False
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(confirm,range(4)))
        self.assertEqual(sum(results),1)
        self.settle()
        self.assertEqual((self.rating(2),self.rating(3)),(1508,1492))
    def test_invalid_scores_and_extra_games(self):
        mid=self.match()
        for games in [[[11,10],[11,2]],[[12,10],[11,9]],[[12,9],[11,5]],[[11,3]],[[11,3],[11,2],[11,4]],[[True,0],[11,2]]]:
            if games==[[12,10],[11,9]]:continue
            with self.assertRaises(AppError):self.report(mid,games=games)
        self.report(mid,games=[[12,10],[11,9]])
    def test_reject_then_correct_report(self):
        mid=self.match();self.report(mid)
        self.call('/matches/'+str(mid),{'action':'reject'},3)
        self.report(mid,uid=3,games=[[5,11],[11,5],[10,12]])
        self.call('/matches/'+str(mid),{'action':'confirm'},2)
        self.settle()
        self.assertEqual(self.rating(3),1508)
    def test_void_replays_later_bands(self):
        first=self.match();self.report(first);self.call('/matches/'+str(first),{'action':'confirm'},3);self.settle()
        second=self.match();self.report(second);self.call('/matches/'+str(second),{'action':'confirm'},3);self.settle()
        self.assertEqual(self.rating(2),1515) # 16 point gap, expected win +7
        with self.assertRaises(AppError):self.call('/admin/void',{'match_id':first,'reason':'误报赛果'},2)
        self.call('/admin/void',{'match_id':first,'reason':'误报赛果'},1)
        self.settle()
        self.assertEqual((self.rating(2),self.rating(3)),(1508,1492))
        with db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM rating_log').fetchone()[0],2)
    def test_third_party_cannot_report_or_confirm(self):
        mid=self.match()
        with self.assertRaises(AppError):self.report(mid,4)
        self.report(mid)
        with self.assertRaises(AppError):self.call('/matches/'+str(mid),{'action':'confirm'},4)
    def test_public_data_does_not_leak_email_or_pending(self):
        mid=self.match()
        result=self.call('/players',uid=None)
        self.assertNotIn('email',result['players'][0])
        self.assertEqual(self.call('/matches',uid=None)['matches'],[])
        self.assertEqual(self.call('/bootstrap',uid=4)['mine'],[])
    def test_disable_cancels_pending_and_keeps_admin(self):
        mid=self.match()
        self.call('/admin/user',{'user_id':3,'active':False},1)
        with db.connect() as c:self.assertEqual(c.execute('SELECT status FROM matches WHERE id=?',(mid,)).fetchone()[0],'cancelled')
        with self.assertRaises(AppError):self.call('/admin/user',{'user_id':1,'active':False},1)
    def test_report_before_start_allowed(self):
        mid=self.match()
        with db.transaction() as c:c.execute('UPDATE matches SET scheduled_at=? WHERE id=?',((datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),mid))
        self.report(mid)
        self.assertEqual(self.rating(2),1500)
    def test_origin_and_csrf_guards(self):
        def request(path,body,origin):
            raw=json.dumps(body).encode();env={'PATH_INFO':config.BASE+'/api/'+path,'REQUEST_METHOD':'POST','CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw),'HTTP_ORIGIN':origin};out={}
            def start(status,headers):out['status']=status
            list(application(env,start));return out['status']
        self.assertTrue(request('auth/login',{'identity':'球友2','password':'correct-password'},'https://evil.example').startswith('403'))
        self.assertTrue(request('profile',{},config.ORIGIN).startswith('401'))
    def test_recovery_revokes_and_rotates(self):
        r=self.call('/auth/recover',{'email':'p2@example.com','recovery_code':'recovery-code-2','password':'replacement-pass'},None)
        self.assertTrue(r['recovery_code'])
        with self.assertRaises(AppError):self.call('/auth/recover',{'email':'p2@example.com','recovery_code':'recovery-code-2','password':'replacement-pass'},None)
        login=self.call('/auth/login',{'identity':'p2@example.com','password':'replacement-pass'},None)
        self.assertEqual(login['user']['id'],2)

    def test_avatar_upload_reencodes_and_preserves_rating(self):
        import base64
        from PIL import Image
        raw=io.BytesIO()
        image=Image.new('RGBA',(400,300),(255,0,0,128))
        image.save(raw,format='PNG')
        result=self.call('/profile/avatar',{'image':base64.b64encode(raw.getvalue()).decode()})
        self.assertEqual(len(result['avatar_version']),20)
        with db.connect() as c:
            row=c.execute('SELECT avatar,rating,avatar_version FROM users WHERE id=2').fetchone()
        self.assertEqual(row['rating'],1500)
        with Image.open(io.BytesIO(row['avatar'])) as saved:
            self.assertEqual(saved.format,'JPEG')
            self.assertEqual(saved.size,(256,256))
            self.assertEqual(len(saved.getexif()),0)
        players=self.call('/players',uid=None)['players']
        player=next(p for p in players if p['id']==2)
        self.assertEqual(player['avatar_version'],result['avatar_version'])
        self.assertNotIn('avatar',player)
        response={}
        def start(status,headers):response.update(status=status,headers=dict(headers))
        data=b''.join(application({'PATH_INFO':config.BASE+'/api/avatars/2/'+result['avatar_version']+'.jpg','REQUEST_METHOD':'GET'},start))
        self.assertTrue(response['status'].startswith('200'))
        self.assertEqual(response['headers']['Content-Type'],'image/jpeg')
        self.assertEqual(data,row['avatar'])
    def test_avatar_rejects_invalid_and_oversized_images(self):
        import base64
        from PIL import Image
        for image in ['not valid base64!',base64.b64encode(b'<svg onload="alert(1)"></svg>').decode(),'A'*280001]:
            with self.assertRaises(AppError):self.call('/profile/avatar',{'image':image})
        output=io.BytesIO();Image.new('RGB',(1025,100)).save(output,format='PNG')
        with self.assertRaises(AppError):self.call('/profile/avatar',{'image':base64.b64encode(output.getvalue()).decode()})
    def test_v1_avatar_migration_preserves_existing_accounts(self):
        original_db=config.DB
        config.DB=config.DATA/'old.sqlite3'
        try:
            with db.connect() as c:
                c.executescript(db.SCHEMA)
                c.execute("INSERT INTO users(email,name,password,role,rating,recovery_hash,blade) VALUES('existing@example.com','老球友','hashed','admin',1625,'recovery','已有底板')")
            db.init()
            db.init() # idempotent migration
            with db.connect() as c:
                u=c.execute('SELECT * FROM users').fetchone()
                self.assertEqual((u['name'],u['rating'],u['role'],u['blade']),('老球友',1625,'admin','已有底板'))
                self.assertEqual(u['avatar_version'],'')
        finally:
            config.DB=original_db

    def test_void_waits_for_midnight_and_does_not_settle_today(self):
        old=self.match();self.report(old);self.call('/matches/'+str(old),{'action':'confirm'},3);self.settle()
        today=self.match();self.report(today);self.call('/matches/'+str(today),{'action':'confirm'},3)
        self.call('/admin/void',{'match_id':old,'reason':'夜间统一更正'},1)
        self.assertEqual(self.rating(2),1508)
        with db.transaction() as c:
            self.assertEqual(ratings.settle_due(c),0)
        self.assertEqual(self.rating(2),1508)
        with db.transaction() as c:
            c.execute("UPDATE settings SET value=? WHERE key='rating_rebuild_pending'",((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(timespec='milliseconds').replace('+00:00','Z'),))
            ratings.settle_due(c)
        self.assertEqual(self.rating(2),1500)
        with db.connect() as c:self.assertIsNone(c.execute('SELECT settled_at FROM matches WHERE id=?',(today,)).fetchone()[0])
    def test_custom_title_limit_and_user_ownership(self):
        self.call('/profile/title',{'title':'横板暴力熊','user_id':3},2)
        with db.connect() as c:
            a=c.execute('SELECT title FROM users WHERE id=2').fetchone()[0]
            b=c.execute('SELECT title FROM users WHERE id=3').fetchone()[0]
        self.assertEqual(a,'横板暴力熊');self.assertEqual(b,'')
        self.call('/profile/title',{'title':'一二三四五六七八九十'},2)
        with self.assertRaises(AppError):self.call('/profile/title',{'title':'一二三四五六七八九十一'},2)
        players=self.call('/players',uid=None)['players']
        self.assertEqual(next(p for p in players if p['id']==2)['title'],'一二三四五六七八九十')
        self.call('/profile/title',{'title':''},2)
        with db.connect() as c:self.assertEqual(c.execute('SELECT title FROM users WHERE id=2').fetchone()[0],'')
    def test_same_pair_can_schedule_multiple_matches(self):
        first=self.match();second=self.match()
        self.assertNotEqual(first,second)
    def test_revoked_session_cannot_mutate_after_auth_snapshot(self):
        with db.transaction() as c:c.execute("DELETE FROM sessions WHERE token='fake-2'")
        with self.assertRaises(AppError):self.call('/profile',{'blade':'stale'})
    def test_doubles_four_consents_and_no_individual_rating(self):
        date=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        mid=self.call('/doubles',{'partner_id':3,'opponent1_id':1,'opponent2_id':4,'best_of':3,'venue':'测试球馆','scheduled_at':date})['id']
        with self.assertRaises(AppError):self.call('/doubles/'+str(mid),{'action':'report','games':[[11,5],[11,6]]})
        for uid in [1,3,4]:self.call('/doubles/'+str(mid),{'action':'accept'},uid)
        self.call('/doubles/'+str(mid),{'action':'report','games':[[11,5],[11,6]]},2)
        for uid in [1,3]:self.call('/doubles/'+str(mid),{'action':'confirm'},uid)
        self.assertEqual(self.call('/doubles',uid=None)['doubles'],[])
        self.call('/doubles/'+str(mid),{'action':'confirm'},4)
        self.assertEqual(len(self.call('/doubles',uid=None)['doubles']),1)
        self.assertEqual([self.rating(i) for i in [1,2,3,4]],[1500]*4)
        stats=self.call('/analytics',uid=None)
        self.assertEqual(stats['doubles_combos'][0]['rate'],1)
        self.assertEqual(stats['activity'][0]['doubles'],1)
        with self.assertRaises(AppError):self.call('/doubles/'+str(mid),{'action':'confirm'},4)
    def test_doubles_reject_resets_all_confirmations(self):
        mid=self.call('/doubles',{'partner_id':3,'opponent1_id':1,'opponent2_id':4,'best_of':3,'venue':'测试球馆','scheduled_at':datetime.now(timezone.utc).isoformat()})['id']
        for uid in [1,3,4]:self.call('/doubles/'+str(mid),{'action':'accept'},uid)
        self.call('/doubles/'+str(mid),{'action':'report','games':[[11,2],[11,3]]})
        self.call('/doubles/'+str(mid),{'action':'confirm'},3)
        self.call('/doubles/'+str(mid),{'action':'reject'},4)
        with db.connect() as c:self.assertEqual(c.execute('SELECT sum(confirmed) FROM doubles_consents WHERE match_id=?',(mid,)).fetchone()[0],0)
        with self.assertRaises(AppError):self.call('/doubles/'+str(mid),{'action':'accept'},5)
    def test_midnight_settlement_excludes_today_and_is_idempotent(self):
        from zoneinfo import ZoneInfo
        old=self.match();self.report(old);self.call('/matches/'+str(old),{'action':'confirm'},3)
        today=self.match();self.report(today);self.call('/matches/'+str(today),{'action':'confirm'},3)
        midnight=datetime.now(ZoneInfo('Asia/Shanghai')).replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
        with db.transaction() as c:
            c.execute('UPDATE matches SET confirmed_at=? WHERE id=?',((midnight-timedelta(milliseconds=1)).isoformat(timespec='milliseconds').replace('+00:00','Z'),old))
            c.execute('UPDATE matches SET confirmed_at=? WHERE id=?',(midnight.isoformat(timespec='milliseconds').replace('+00:00','Z'),today))
            self.assertEqual(ratings.settle_due(c),1)
            self.assertEqual(ratings.settle_due(c),0)
        self.assertEqual(self.rating(2),1508)
        with db.connect() as c:self.assertIsNone(c.execute('SELECT settled_at FROM matches WHERE id=?',(today,)).fetchone()[0])
    def event(self,groups=2,qualifiers=1,players=range(1,5)):
        eid=self.call('/events',{'title':'测试周赛','kind':'weekly','venue':'测试球馆','starts_at':datetime.now(timezone.utc).isoformat(),'best_of':3,'group_count':groups,'qualifiers':qualifiers},1)['id']
        for uid in players:self.call('/events/'+str(eid)+'/join',{},uid)
        return eid
    def finish_fixture(self,m):
        self.call('/matches/'+str(m['id']),{'action':'report','games':[[11,4],[11,5]]},m['a_id'])
        self.call('/matches/'+str(m['id']),{'action':'confirm'},m['b_id'])
    def test_event_group_draw_seeds_and_full_knockout(self):
        eid=self.event(2,2)
        self.call(f'/events/{eid}/seeds',{'seeds':[{'user_id':2,'seed':1},{'user_id':3,'seed':2}]},1)
        self.call(f'/events/{eid}/draw',{},1)
        e=self.call(f'/events/{eid}',uid=None)
        seeds=[p for p in e['entries'] if p['seed']]
        self.assertNotEqual(seeds[0]['group_no'],seeds[1]['group_no'])
        with self.assertRaises(AppError):self.call(f'/events/{eid}/advance',{'qualifiers':2},1)
        for m in e['matches']:self.finish_fixture(m)
        self.call(f'/events/{eid}/advance',{'qualifiers':2},1)
        e=self.call(f'/events/{eid}',uid=None)
        self.assertEqual(len([m for m in e['matches'] if m['phase']=='knockout']),2)
        for m in [m for m in e['matches'] if m['phase']=='knockout']:self.finish_fixture(m)
        e=self.call(f'/events/{eid}',uid=None)
        final=[m for m in e['matches'] if m['round_no']==2][0]
        self.finish_fixture(final)
        e=self.call(f'/events/{eid}',uid=None)
        self.assertEqual(e['status'],'completed')
        self.assertEqual(e['champion_id'],final['a_id'])
        self.assertEqual([self.rating(i) for i in range(1,5)],[1500]*4)
        with self.assertRaises(AppError):self.call('/admin/void',{'match_id':final['id'],'reason':'禁止破坏晋级链'},1)
    def test_event_byes_progress_and_cancel(self):
        eid=self.event(3,1,range(1,7));self.call(f'/events/{eid}/draw',{},1)
        e=self.call(f'/events/{eid}',uid=None)
        for m in e['matches']:self.finish_fixture(m)
        self.call(f'/events/{eid}/advance',{'qualifiers':1},1)
        e=self.call(f'/events/{eid}',uid=None)
        self.assertEqual(sum(1 for b in e['bracket'] if b['round_no']==1 and b['winner_id']),1)
        for m in [m for m in e['matches'] if m['phase']=='knockout']:self.finish_fixture(m)
        e=self.call(f'/events/{eid}',uid=None)
        self.assertEqual(len([m for m in e['matches'] if m['round_no']==2]),1)
        self.call(f'/events/{eid}/cancel',{'reason':'测试取消整场赛事'},1)
        self.assertEqual(self.call(f'/events/{eid}',uid=None)['status'],'cancelled')
    def test_event_permissions_and_seed_validation(self):
        with self.assertRaises(AppError):self.call('/events',{'title':'越权创建'},2)
        eid=self.event()
        with self.assertRaises(AppError):self.call(f'/events/{eid}/draw',{},2)
        with self.assertRaises(AppError):self.call(f'/events/{eid}/seeds',{'seeds':[{'user_id':2,'seed':1},{'user_id':3,'seed':1}]},1)
        self.call(f'/events/{eid}/draw',{},1)
        with self.assertRaises(AppError):self.call(f'/events/{eid}/join',{},5)
    def test_capped_round_robin_starts_on_time_and_finishes_without_knockout(self):
        future = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        eid = self.call('/events',{'title':'测试大循环','kind':'weekly','venue':'测试球馆','starts_at':future,
            'best_of':5,'group_count':1,'qualifiers':0,'rating_ceiling':1600},1)['id']
        with db.transaction() as c:
            c.execute('UPDATE users SET rating=1600 WHERE id=6')
        with self.assertRaises(AppError):
            self.call(f'/events/{eid}/join',{},6)
        for uid in [2,3,4]:
            self.call(f'/events/{eid}/join',{},uid)
        with self.assertRaises(AppError), db.transaction() as c:
            events.start_round_robin(c,eid)
        with db.transaction() as c:
            past = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
            c.execute('UPDATE events SET starts_at=? WHERE id=?',(past,eid))
        with self.assertRaises(AppError):
            self.call(f'/events/{eid}/join',{},5)
        with db.transaction() as c:
            self.assertEqual(events.start_round_robin(c,eid)['status'],'groups')
            self.assertEqual(events.start_round_robin(c,eid),{'status':'groups'})
        event = self.call(f'/events/{eid}',uid=None)
        self.assertEqual({frozenset([m['a_id'],m['b_id']]) for m in event['matches']},
                         {frozenset([2,3]),frozenset([2,4]),frozenset([3,4])})
        self.assertEqual(len(event['matches']),3)
        self.assertFalse(event['registration_open'])
        with self.assertRaises(AppError):
            self.call(f'/events/{eid}/advance',{'qualifiers':2},1)
        for m in event['matches']:
            self.assertEqual(m['best_of'],5)
            self.report(m['id'],m['a_id'],[[11,4],[11,5],[11,6]])
            self.call(f'/matches/{m["id"]}',{'action':'confirm'},m['b_id'])
        event = self.call(f'/events/{eid}',uid=None)
        self.assertEqual(event['status'],'completed')
        self.assertEqual(event['champion_id'],event['groups'][0]['standings'][0]['user_id'])
        self.assertEqual(event['bracket'],[])
        self.assertEqual([self.rating(uid) for uid in [2,3,4]],[1500,1500,1500])
    def test_relationship_boards_and_two_week_changes(self):
        mid=self.match();self.report(mid);self.call('/matches/'+str(mid),{'action':'confirm'},3)
        before=self.call('/analytics',uid=None)
        self.assertEqual(before['activity'][0]['played'],1)
        self.assertEqual(before['risers'],[])
        self.assertTrue(all(r['wins']>0 for r in before['benefactors']))
        self.assertTrue(all(r['wins']<r['played'] for r in before['nemeses']))
        self.settle()
        after=self.call('/analytics',uid=None)
        self.assertEqual(after['risers'][0]['delta'],8)
        self.assertEqual(after['fallers'][0]['delta'],-8)
    def test_event_absence_forfeits_pending_only_and_settles_at_midnight(self):
        eid=self.event(1,1,[2,3,4,5])
        self.call(f'/events/{eid}/draw',{},1)
        event=self.call(f'/events/{eid}',uid=None)
        reported=next(m for m in event['matches'] if {m['a_id'],m['b_id']}=={2,3})
        self.report(reported['id'],reported['a_id'])
        with self.assertRaises(AppError):
            self.call(f'/events/{eid}/absent',{'user_ids':[2],'reason':'未到场'},3)
        with self.assertRaises(AppError):
            self.call(f'/events/{eid}/absent',{'user_ids':[6],'reason':'未到场'},1)
        result=self.call(f'/events/{eid}/absent',{'user_ids':[2],'reason':'报名后未到场'},1)
        self.assertEqual(result['affected_matches'],2)
        event=self.call(f'/events/{eid}',uid=None)
        for m in event['matches']:
            if m['forfeit_reason']:
                self.assertNotEqual(m['winner_id'],2)
                self.assertEqual(sorted([m['score_a'],m['score_b']]),[0,2])
                self.assertEqual(json.loads(m['games']),[])
                self.assertIsNone(m['settled_at'])
        self.assertEqual(next(m for m in event['matches'] if m['id']==reported['id'])['status'],'reported')
        self.assertEqual(self.rating(2),1500)
        self.assertEqual(self.call(f'/events/{eid}/absent',{'user_ids':[2],'reason':'未到场'},1)['affected_matches'],0)
        self.assertEqual(self.settle(),2)
        self.assertLess(self.rating(2),1500)

    def test_both_absent_record_losses_without_creating_a_winner(self):
        eid=self.event(1,1,[2,3,4])
        self.call(f'/events/{eid}/draw',{},1)
        self.call(f'/events/{eid}/absent',{'user_ids':[2],'reason':'未到场'},1)
        self.call(f'/events/{eid}/absent',{'user_ids':[3],'reason':'未到场'},1)
        event=self.call(f'/events/{eid}',uid=None)
        pair=next(m for m in event['matches'] if {m['a_id'],m['b_id']}=={2,3})
        self.assertEqual(pair['status'],'completed')
        self.assertIsNone(pair['winner_id'])
        self.assertEqual([pair['score_a'],pair['score_b']],[0,0])
        table=event['groups'][0]['standings']
        self.assertEqual(next(p for p in table if p['user_id']==2)['wins'],0)
        self.assertEqual(next(p for p in table if p['user_id']==3)['wins'],0)
        self.settle()
        with db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM rating_log WHERE match_id=?',(pair['id'],)).fetchone()[0],0)
        self.call('/analytics',uid=None)

if __name__=='__main__':unittest.main()
