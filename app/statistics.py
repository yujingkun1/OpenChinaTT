from collections import defaultdict
from datetime import datetime,timedelta,timezone
from app import accounts

def collect(c):
    players={r['id']:accounts.public(r) for r in c.execute('SELECT * FROM users WHERE active=1')}
    activity={pid:dict(p,played=0,singles=0,doubles=0) for pid,p in players.items()}
    relations=defaultdict(lambda:[0,0])
    for m in c.execute("SELECT * FROM matches WHERE status='completed'"):
        for uid,opponent in [(m['a_id'],m['b_id']),(m['b_id'],m['a_id'])]:
            if uid in activity:
                activity[uid]['played']+=1;activity[uid]['singles']+=1
            relations[(uid,opponent)][0]+=1
            relations[(uid,opponent)][1]+=int(m['winner_id']==uid)
    pairs=defaultdict(lambda:[0,0])
    doubles_players=defaultdict(lambda:[0,0])
    for m in c.execute("SELECT * FROM doubles_matches WHERE status='completed'"):
        for ids,won in [([m['a1'],m['a2']],m['score_a']>m['score_b']),([m['b1'],m['b2']],m['score_b']>m['score_a'])]:
            pair=tuple(sorted(ids));pairs[pair][0]+=1;pairs[pair][1]+=int(won)
            for uid in ids:
                doubles_players[uid][0]+=1;doubles_players[uid][1]+=int(won)
                if uid in activity:activity[uid]['played']+=1;activity[uid]['doubles']+=1
    rivals=[]
    for (uid,oid),(played,wins) in relations.items():
        if uid in players and oid in players:
            rivals.append(dict(player=players[uid],opponent=players[oid],played=played,wins=wins,rate=wins/played))
    combos=[]
    for (a,b),(played,wins) in pairs.items():
        if a in players and b in players:
            combos.append(dict(players=[players[a],players[b]],played=played,wins=wins,rate=wins/played))
    combos.sort(key=lambda p:(-p['rate'],-p['played'],-p['wins'],p['players'][0]['id'],p['players'][1]['id']))
    individual=[]
    for uid,(played,wins) in doubles_players.items():
        if uid not in players:continue
        best=next((pair for pair in combos if any(p['id']==uid for p in pair['players'])),None)
        partner=next((p for p in best['players'] if p['id']!=uid),None) if best else None
        individual.append(dict(player=players[uid],played=played,wins=wins,rate=wins/played,best_partner=partner,best_partner_played=best['played'] if best else 0,best_partner_rate=best['rate'] if best else 0))
    individual.sort(key=lambda p:(-p['rate'],-p['played'],-p['wins'],p['player']['id']))
    cutoff=(datetime.now(timezone.utc)-timedelta(days=14)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    changes=[]
    for r in c.execute('''SELECT l.user_id,sum(l.delta) delta,count(*) played FROM rating_log l JOIN matches m ON m.id=l.match_id WHERE m.settled_at>=? GROUP BY l.user_id''',(cutoff,)):
        if r['user_id'] in players:changes.append(dict(player=players[r['user_id']],delta=r['delta'],played=r['played']))
    return {
        'activity':sorted([a for a in activity.values() if a['played']],key=lambda x:(-x['played'],x['id']))[:100],
        'benefactors':sorted([r for r in rivals if r['wins']>0],key=lambda x:(-x['rate'],-x['played'],x['player']['id'],x['opponent']['id']))[:100],
        'nemeses':sorted([r for r in rivals if r['wins']<r['played']],key=lambda x:(x['rate'],-x['played'],x['player']['id'],x['opponent']['id']))[:100],
        'risers':sorted([x for x in changes if x['delta']>0],key=lambda x:(-x['delta'],-x['played'],x['player']['id']))[:100],
        'fallers':sorted([x for x in changes if x['delta']<0],key=lambda x:(x['delta'],-x['played'],x['player']['id']))[:100],
        'doubles_combos':combos[:100],'doubles_players':individual[:100],'window_days':14,
        'policy':'胜率以已确认且未作废比赛计算，至少1场，显示样本数；同胜率场次多者优先。近两周为最近14天按积分结算时间统计的单打积分净变化。'
    }
