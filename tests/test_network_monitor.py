import importlib.util
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('network_monitor', Path(__file__).resolve().parents[1]/'deploy/libreelec/network-monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class NetworkMonitorTests(unittest.TestCase):
    def test_distinguishes_endpoint_dns_and_vpn_evidence(self):
        checks = {'internet_ip_1': {'state':'ok'}, 'internet_ip_2': {'state':'ok'},
                  'internet_dns': {'state':'ok'}, 'source': {'state':'ok'}}
        self.assertEqual(monitor.classify(checks, {}), 'endpoints_reachable_stream_quality_unknown')
        checks['source']['state'] = 'tcp_failed'
        self.assertEqual(monitor.classify(checks, {}), 'provider_endpoint_or_route_problem')
        checks['internet_dns']['state'] = 'dns_failed'
        self.assertEqual(monitor.classify(checks, {}), 'dns_problem')
        for key in ('internet_ip_1','internet_ip_2'): checks[key]['state']='tcp_failed'
        self.assertEqual(monitor.classify(checks, {'state':'present','freshest_handshake_seconds':200}), 'internet_unreachable_vpn_handshake_stale')
        self.assertEqual(monitor.classify(checks, {'state':'present','freshest_handshake_seconds':30}), 'internet_or_route_problem')

    def test_dns_errors_never_include_raw_exception(self):
        with patch.object(monitor.socket, 'getaddrinfo', side_effect=OSError('private-provider-password')):
            self.assertEqual(monitor.probe('private-provider',443), {'state':'dns_failed'})

    def test_sources_are_read_without_changing_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            with sqlite3.connect(directory/'mijntv.db') as db:
                db.execute('CREATE TABLE settings (key TEXT, value TEXT)')
                db.execute('INSERT INTO settings VALUES (?,?)', ('source','https://example.test:8443/get.php?username=private&password=secret'))
            self.assertEqual(monitor.targets(directory)['source'], ('example.test',8443))
            self.assertNotIn('secret', str(monitor.targets(directory)))
