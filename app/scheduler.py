"""Portable daily settlement worker for Docker/standalone deployments."""
import json
import logging
import signal
import threading
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from app import db,ratings

log=logging.getLogger('openchinatt-settler')
logging.basicConfig(level=logging.INFO,format='%(message)s')

def main():
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    db.init()
    while not stop.is_set():
        try:
            with db.transaction() as c:
                count=ratings.settle_due(c)
            log.info(json.dumps({'event':'ratings.settled','matches':count}))
        except Exception:
            log.exception(json.dumps({'event':'ratings.settlement_failed'}))
            stop.wait(60)
            continue
        now=datetime.now(ZoneInfo('Asia/Shanghai'))
        next_run=(now+timedelta(days=1)).replace(hour=0,minute=0,second=1,microsecond=0)
        stop.wait(max(1,(next_run-now).total_seconds()))

if __name__=='__main__':
    main()
