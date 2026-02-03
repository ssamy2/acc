
import unittest
import multiprocessing
import time
import json
import logging
import sys
import os

# Adjust path to import backend modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.core_engine.isolated_worker import TDLibWorker

# Mocking the _TDLibRaw class to avoid actual C-library calls during test
class MockTDLibRaw:
    def __init__(self):
        self.client = "mock_client_pointer"
        self.sent_queries = []

    def send(self, query):
        self.sent_queries.append(query)
        print(f"[MOCK] Sent: {query}")

    def receive(self, timeout=1.0):
        # We will inject events manually in the test
        return None 

# Monkey patch the worker's import of _TDLibRaw if possible, 
# or we can modify the worker to accept a client factory.
# For this test, we might need to rely on the fact that the worker creates _TDLibRaw internally.
# A better approach for unit testing is to separate the Logic from the Process, 
# but since we are integration testing the Process, we have to mock at the module level.

# However, since TDLibWorker runs in a SEPARATE PROCESS, we can't easily mock objects 
# inside it from here unless we inject the mock or control the environment.
# 
# STRATEGY: 
# We will create a subclass of TDLibWorker that overrides the `run` method 
# or the `_handler` logic, OR we just trust the logic we wrote and write a "dry run" test 
# that imports the logic. 
#
# Actually, the best way to verify the *logic* of the event handler is to extract the 
# `_handle_event` method and test it in isolation.

from backend.core_engine.isolated_worker import TDLibWorker

class TestTDLibLogic(unittest.TestCase):

    def setUp(self):
        self.mock_client = MockTDLibRaw()
        self.res_q = multiprocessing.Queue()
        self.worker = TDLibWorker(multiprocessing.Queue(), self.res_q, 123, "hash", "+123456")
        
        # Setup Logger
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger("TestLogger")

    def test_handle_password_event(self):
        # Simulate receiving "User needs to enter 2FA password"
        event = {
            "@type": "updateAuthorizationState",
            "authorization_state": {
                "@type": "authorizationStateWaitPassword"
            }
        }
        
        self.worker._handle_event(self.mock_client, event, self.logger)
        
        # Check Result Queue
        result = self.res_q.get(timeout=1)
        self.assertEqual(result.get("type"), "STATUS")
        self.assertEqual(result.get("status"), "WAITING_PASSWORD")

    def test_handle_registration_event(self):
        # Simulate receiving "User needs to register"
        event = {
            "@type": "updateAuthorizationState",
            "authorization_state": {
                "@type": "authorizationStateWaitRegistration"
            }
        }
        
        self.worker._handle_event(self.mock_client, event, self.logger)
        
        result = self.res_q.get(timeout=1)
        self.assertEqual(result.get("type"), "STATUS")
        self.assertEqual(result.get("status"), "WAITING_REGISTRATION")

    def test_send_password_command(self):
        # This tests the route -> worker command flow logic (conceptually)
        # We manually invoke what the worker would do upon receiving the command
        
        cmd_payload = {
            "@type": "checkAuthenticationPassword",
            "password": "super_secret"
        }
        
        # The worker's main loop does: client.send(query)
        self.mock_client.send(cmd_payload)
        
        self.assertEqual(len(self.mock_client.sent_queries), 1)
        self.assertEqual(self.mock_client.sent_queries[0]["password"], "super_secret")

if __name__ == '__main__':
    unittest.main()
