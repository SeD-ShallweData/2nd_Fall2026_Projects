"""Linux runtime adapter; existing analysis modules and local launcher stay untouched."""
from pathlib import Path
import argparse
import json
import os
import socket
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
WORK=ROOT/'Code/sk_admin_mapping'
sys.path.insert(0,str(WORK))
from state_store import Store, ProviderBudget


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--settings',type=Path,required=True)
    args=parser.parse_args()
    config=json.loads(args.settings.read_text(encoding='utf-8'))
    if config.get('mode')!='dashboard' or config.get('gcp_data_transfer_approved') is not True:
        raise ValueError('GCP_DASHBOARD_DATA_ADMISSION_REQUIRED')
    os.environ.update(ROA_ENABLE_LLM='1',ROA_USE_WELFARE_API_CACHE='1',
                      LLM_ENV_FILE=config['llm_env_file'],
                      SEOUL_ENV_FILE=config.get('seoul_env_file','/etc/bbigeudeok/seoul-not-configured.env'),
                      LLM_TIMEOUT_SECONDS='20',TZ='Asia/Seoul',
                      MPLCONFIGDIR=str(Path(config['state_db']).parent/'matplotlib'))
    if hasattr(time,'tzset'):time.tzset()
    store=Store(config['state_db'])
    store.seed('llm_question',config.get('baseline_llm_questions',0))
    store.seed('provider',config.get('baseline_provider_reservations',0))
    import request_limits
    request_limits._shared=ProviderBudget(store)
    from migration import install_verified_cache_adapter
    install_verified_cache_adapter(ROOT,ROOT/'migration_inventory.local.json')
    import spatial_explorer
    from welfare_api import load_verified_api_cache
    server=spatial_explorer.make_server(8766)
    load_verified_api_cache()  # One warm-up before admitting browser concurrency.
    print('DASHBOARD_READY: verified migrated bytes; persistent provider budget',flush=True)
    if os.environ.get('NOTIFY_SOCKET'):
        address=os.environ['NOTIFY_SOCKET']
        if address.startswith('@'):address='\0'+address[1:]
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as notify:
            notify.connect(address)
            notify.sendall(b'READY=1')
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':
    sys.dont_write_bytecode=True
    main()
