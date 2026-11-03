import argparse
import json
import secrets
import sys
from app import db,config
from app.security import digest,hash_password

def main():
    p = argparse.ArgumentParser()
    p.add_argument('command',choices=['init','bootstrap','backup','settle'])
    p.add_argument('--output')
    args = p.parse_args()
    db.init()
    if args.command=='bootstrap':
        # Credentials are emitted only to a private caller, never application logs.
        with db.transaction() as c:
            if c.execute("SELECT id FROM users WHERE role='admin'").fetchone():
                print(json.dumps({'existing':True}))
                return
            pw = secrets.token_urlsafe(16)
            recovery = secrets.token_urlsafe(24)
            c.execute("INSERT INTO users(email,name,password,role,recovery_hash) VALUES(?, ?, ?, 'admin', ?)",(config.ADMIN_EMAIL,config.ADMIN_NAME,hash_password(pw),digest(recovery)))
            print(json.dumps({'email':config.ADMIN_EMAIL,'name':config.ADMIN_NAME,'password':pw,'recovery_code':recovery},ensure_ascii=False))
    if args.command=='settle':
        from app.ratings import settle_due
        with db.transaction() as c:
            count=settle_due(c)
        print(json.dumps({'settled_matches':count}))
    if args.command=='backup':
        if not args.output:
            p.error('--output required')
        import sqlite3
        with db.connect() as source, sqlite3.connect(args.output) as target:
            source.backup(target)

if __name__=='__main__':
    main()
