"""Process-wide outbound request budget. No identity, query or credential storage."""
from collections import deque
import os
import threading
import time

class RequestBudget:
    def __init__(self,per_minute=12,total=120,clock=time.monotonic):
        if type(per_minute) is not int or not 1<=per_minute<=120 or type(total) is not int or not 1<=total<=10000:
            raise ValueError('INVALID_REQUEST_LIMITS')
        self.per_minute,self.total,self.clock=per_minute,total,clock
        self.used=0;self.window=deque();self.lock=threading.Lock()
    @classmethod
    def from_env(cls):
        return cls(int(os.environ.get('LLM_MAX_REQUESTS_PER_MINUTE','12')),int(os.environ.get('LLM_MAX_REQUESTS_PER_PROCESS','120')))
    def take(self):
        with self.lock:
            now=self.clock()
            while self.window and now-self.window[0]>=60:self.window.popleft()
            if self.used>=self.total:return 'LLM_PROCESS_BUDGET_EXHAUSTED'
            if len(self.window)>=self.per_minute:return 'LLM_RATE_LIMIT'
            self.window.append(now);self.used+=1
    def status(self):
        with self.lock:return {'max_per_minute':self.per_minute,'max_per_process':self.total,'consumed':self.used,'scope':'SINGLE_PROCESS_RESETS_ON_RESTART'}

_shared=None
_lock=threading.Lock()
def process_budget():
    global _shared
    with _lock:
        if _shared is None:_shared=RequestBudget.from_env()
        return _shared
