"""Public-edge monitoring must fail when local-only health would look normal."""
import io
import json
from unittest.mock import patch
import unittest
from healthcheck import public_https_ready

class Connection:
    status=200
    body=b'{"request_budget":{"scope":"persistent"}}'
    def __init__(self,host,timeout):self.host=host
    def request(self,method,path,headers):
        self.request_values=(method,path,headers)
    def getresponse(self):return self
    def read(self,limit):return self.body
    def close(self):pass

class PublicHealthTests(unittest.TestCase):
    def dns(self,ip='8.8.8.8'):
        return io.BytesIO(json.dumps({'Answer':[{'type':1,'data':ip}]}).encode())
    def test_valid_public_https_authenticated_response(self):
        with patch('healthcheck.urlopen',return_value=self.dns()),patch('healthcheck.HTTPSConnection',Connection):
            self.assertTrue(public_https_ready('synthetic.tailunit.ts.net','private-cookie'))
    def test_tailnet_private_dns_is_not_accepted_as_public_proof(self):
        with patch('healthcheck.urlopen',return_value=self.dns('100.110.62.31')):
            self.assertFalse(public_https_ready('synthetic.tailunit.ts.net','private-cookie'))
    def test_dns_outage_does_not_report_healthy(self):
        with patch('healthcheck.urlopen',side_effect=OSError):
            self.assertFalse(public_https_ready('synthetic.tailunit.ts.net','private-cookie'))
    def test_tls_failure_does_not_report_healthy(self):
        with patch('healthcheck.urlopen',return_value=self.dns()),patch('healthcheck.HTTPSConnection',side_effect=OSError):
            self.assertFalse(public_https_ready('synthetic.tailunit.ts.net','private-cookie'))
    def test_unauthed_public_response_does_not_report_healthy(self):
        class Unauthorized(Connection):status=401
        with patch('healthcheck.urlopen',return_value=self.dns()),patch('healthcheck.HTTPSConnection',Unauthorized):
            self.assertFalse(public_https_ready('synthetic.tailunit.ts.net','private-cookie'))

if __name__=='__main__':unittest.main(verbosity=2)
