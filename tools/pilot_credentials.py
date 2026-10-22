#!/usr/bin/env python3
"""Issue a pilot token to a private file, keeping only its hash on the server."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--registry',type=Path,required=True)
    p.add_argument('--token-file',type=Path,required=True)
    p.add_argument('--id',required=True)
    p.add_argument('--role',choices=['tester','operator'],default='tester')
    p.add_argument('--days',type=int,default=30)
    p.add_argument('--daily-limit',type=int,default=20)
    args=p.parse_args()
    if not re.fullmatch(r'[a-z0-9_-]{1,64}',args.id) or not 1<=args.days<=90 or not 1<=args.daily_limit<=100:
        p.error('invalid principal, expiry, or daily limit')
    os.umask(0o077)
    data=json.loads(args.registry.read_text()) if args.registry.exists() else {'credentials':[]}
    if any(r['id']==args.id for r in data['credentials']):
        p.error('principal already exists; rotate explicitly in the registry')
    token=secrets.token_hex(32)
    # Exclusive creation prevents accidentally overwriting a usable credential.
    with args.token_file.open('x') as f:f.write(token+'\n')
    data['credentials'].append(dict(id=args.id,role=args.role,expiresAt=time.time()+args.days*86400,
        dailyLimit=args.daily_limit,pendingLimit=2,sha256=hashlib.sha256(token.encode()).hexdigest()))
    tmp=args.registry.with_suffix('.new')
    with tmp.open('x') as f:json.dump(data,f,indent=2)
    tmp.replace(args.registry)
    print('Credential written to private files. Token was not printed.')

if __name__=='__main__': main()
