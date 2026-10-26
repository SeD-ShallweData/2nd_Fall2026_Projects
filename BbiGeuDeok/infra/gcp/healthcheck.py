"""Component and authenticated data health; never generates an LLM request."""
from http.client import HTTPConnection
from http.client import HTTPSConnection
from pathlib import Path
import json
import ipaddress
import os
import re
import socket
import sqlite3
import subprocess
import time
from urllib.request import urlopen
from urllib.parse import urlencode


def public_https_ready(host,cookie):
    """Use public DNS and the real public relay, preserving hostname TLS verification."""
    try:
        query=urlencode({'name':host,'type':'A','edns_client_subnet':'0.0.0.0/0'})
        with urlopen('https://dns.google/resolve?'+query,timeout=8) as response:
            answer=json.load(response)
        addresses=[row['data'] for row in answer.get('Answer',[]) if row.get('type')==1]
        public=next(ip for ip in addresses if ipaddress.ip_address(ip).is_global)
        connection=HTTPSConnection(host,timeout=10)
        # HTTPSConnection still uses host for SNI and certificate validation.
        connection._create_connection=lambda address,timeout,source_address: socket.create_connection((public,443),timeout,source_address)
        try:
            connection.request('GET','/api/agent/status',headers={'Host':host,'Cookie':cookie or ''})
            response=connection.getresponse()
            data=response.read(65536)
            return response.status==200 and 'request_budget' in json.loads(data)
        finally:connection.close()
    except (OSError,ValueError,TypeError,StopIteration,KeyError):return False


def systemctl(*args):
    return subprocess.run(['systemctl',*args],capture_output=True,text=True)


def request(port,path,host,body=None,cookie=None):
    c=HTTPConnection('127.0.0.1',port,timeout=8)
    headers={'Host':host}
    if cookie:headers['Cookie']=cookie
    if body is not None:headers.update({'Origin':'https://'+host,'Content-Type':'application/json'})
    try:
        c.request('POST' if body is not None else 'GET',path,body,headers)
        r=c.getresponse();r.read()
        return r.status,r.getheader('Set-Cookie')
    except OSError:return 0,None
    finally:c.close()


def main():
    config=json.loads(Path('/etc/bbigeudeok/settings.json').read_text())
    state=Path(config['state_db']).parent
    host=config['public_host']
    checks={}
    for component in ('backend','gateway'):
        service='bbigeudeok-'+component+'.service'
        if systemctl('is-active',service).stdout.strip()!='active':
            systemctl('start',service)
    checks['backend_http']=request(8766,'/api/agent/status','127.0.0.1:8766')[0]==200
    cookie_path=state/'health_session.secret'
    cookie=cookie_path.read_text().strip() if cookie_path.exists() else None
    status,_=request(8767,'/api/roa/catalog',host,cookie=cookie)
    if status==401:
        secret=Path(config['access_secret_file']).read_text().strip()
        status,new_cookie=request(8767,'/auth/link',host,json.dumps({'token':secret}).encode())
        if status==200 and new_cookie:
            cookie=new_cookie.split(';',1)[0]
            cookie_path.write_text(cookie,encoding='ascii');cookie_path.chmod(0o600)
            status,_=request(8767,'/api/roa/catalog',host,cookie=cookie)
    checks['gateway_authenticated_api']=status==200
    checks['public_dns_tls_authenticated_api']=public_https_ready(host,cookie)
    funnel=subprocess.run(['tailscale','funnel','status','--json'],capture_output=True,text=True)
    try:
        value=json.loads(funnel.stdout)
        checks['funnel_configured']=value.get('AllowFunnel',{}).get(host+':443') is True
    except (ValueError,TypeError):checks['funnel_configured']=False
    if not checks['funnel_configured']:systemctl('restart','bbigeudeok-funnel.service')
    history={}
    try:
        with sqlite3.connect('file:'+config['state_db']+'?mode=ro',uri=True,timeout=5) as db:
            history={'total':db.execute('SELECT count(*) FROM requests').fetchone()[0],
                     'completed':db.execute('SELECT count(*) FROM requests WHERE status=200').fetchone()[0]}
        checks['persistent_state_readable']=True
    except sqlite3.Error:checks['persistent_state_readable']=False
    failures_path=state/'health_failures.json'
    prior=json.loads(failures_path.read_text()) if failures_path.exists() else {}
    healthy=all(checks.values())
    count=0 if healthy else prior.get('consecutive',0)+1
    if count>=3:
        if not checks['backend_http']:systemctl('restart','bbigeudeok-backend.service')
        if not checks['gateway_authenticated_api']:systemctl('restart','bbigeudeok-gateway.service')
        if checks['backend_http'] and checks['gateway_authenticated_api'] and not checks['public_dns_tls_authenticated_api']:
            systemctl('restart','tailscaled.service')
            systemctl('restart','bbigeudeok-funnel.service')
        count=0
    failures_path.write_text(json.dumps({'consecutive':count}))
    pid=systemctl('show','bbigeudeok-backend.service','--property=MainPID','--value').stdout.strip()
    memory={}
    if pid.isdigit() and pid!='0':
        path=Path('/proc')/pid/'status'
        if path.exists():
            raw=path.read_text()
            for name in ('VmRSS','VmHWM'):
                m=re.search(r'^'+name+r':\s+(\d+) kB',raw,re.M)
                if m:memory[name+'_bytes']=int(m.group(1))*1024
    report={'checked_at_unix':time.time(),'healthy':healthy,'checks':checks,
            'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'request_history':history,
            'backend_pid':int(pid) if pid.isdigit() else None,'backend_memory':memory,
            'llm_calls_from_healthcheck':0}
    temp=state/'health.json.tmp'
    temp.write_text(json.dumps(report),encoding='utf-8')
    temp.chmod(0o644);temp.replace(state/'health.json')
    print(json.dumps({'healthy':healthy,'checks':checks}),flush=True)


if __name__=='__main__':main()
