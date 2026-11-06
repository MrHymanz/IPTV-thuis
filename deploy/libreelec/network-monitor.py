#!/usr/bin/python3
"""Independent host network diagnostics; never emit addresses or credentials."""
import argparse
import concurrent.futures
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import socket
import sqlite3
import subprocess
import time
from urllib.parse import urlsplit


def probe(host, port):
    started = time.monotonic()
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return {'state': 'dns_failed'}
    for family, kind, proto, _, address in addresses[:3]:
        try:
            with socket.socket(family, kind, proto) as connection:
                connection.settimeout(2)
                connection.connect(address)
            return {'state': 'ok', 'milliseconds': round((time.monotonic()-started)*1000)}
        except OSError:
            pass
    return {'state': 'tcp_failed'}


def vpn_state():
    try:
        result = subprocess.run(['wg', 'show', 'all', 'latest-handshakes'],
                                capture_output=True, text=True, timeout=3, check=True)
        stamps = [int(line.split()[-1]) for line in result.stdout.splitlines() if line.split()]
        ages = [max(0, int(time.time())-stamp) for stamp in stamps if stamp]
        return {'state': 'present' if stamps else 'absent',
                'freshest_handshake_seconds': min(ages) if ages else None}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'state': 'unknown'}


def targets(directory):
    # Read only three settings, never scan the large channel catalog.
    with sqlite3.connect((directory/'mijntv.db').as_uri()+'?mode=ro', uri=True, timeout=1) as db:
        rows = db.execute("SELECT key,value FROM settings WHERE key IN ('source','backup_source','recording_source')").fetchall()
    result = {'internet_ip_1': ('1.1.1.1', 443),
              'internet_ip_2': ('8.8.8.8', 443),
              'internet_dns': ('example.com', 443)}
    for key, value in rows:
        try:
            url = urlsplit(value.split('|', 1)[0])
            if url.scheme in ('http', 'https') and url.hostname:
                result[key] = (url.hostname, url.port or (443 if url.scheme == 'https' else 80))
        except ValueError:
            pass
    return result


def classify(checks, vpn):
    internet = [checks.get(k, {}).get('state') == 'ok' for k in ('internet_ip_1', 'internet_ip_2')]
    providers = [v['state'] for k,v in checks.items() if k in ('source','backup_source','recording_source')]
    if not any(internet):
        if vpn.get('state') == 'absent':
            return 'internet_unreachable_vpn_absent'
        age = vpn.get('freshest_handshake_seconds')
        if age is not None and age > 180:
            return 'internet_unreachable_vpn_handshake_stale'
        return 'internet_or_route_problem'
    if checks.get('internet_dns', {}).get('state') == 'dns_failed':
        return 'dns_problem'
    if any(state != 'ok' for state in providers):
        return 'provider_endpoint_or_route_problem'
    return 'endpoints_reachable_stream_quality_unknown'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    handler = TimedRotatingFileHandler(directory/'network-diagnostics.log', when='midnight', backupCount=14, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger = logging.getLogger('network-monitor')
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        while True:
            started = time.monotonic()
            try:
                pending = {key: pool.submit(probe, *value) for key,value in targets(directory).items()}
                checks = {key: future.result() for key,future in pending.items()}
                vpn = vpn_state()
                logger.info('network_sample %s', json.dumps({'assessment': classify(checks,vpn), 'checks': checks, 'vpn': vpn}, sort_keys=True))
            except Exception:
                # Exception messages can contain provider URLs or database paths.
                logger.warning('network_sample_unavailable')
            time.sleep(max(1, 15-(time.monotonic()-started)))


if __name__ == '__main__':
    import os
    os.umask(0o077)
    main()
