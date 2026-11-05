import os, tempfile, time, unittest
from ttr_live import TTRLiveStore
from rsi_live import RSILiveStore

class RSILiveStoreTests(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix='.sqlite3'); os.close(fd)
        self.db=TTRLiveStore(path=self.path,backend='sqlite',database_url=None)
        self.store=RSILiveStore(self.db)
    def tearDown(self):
        try: os.remove(self.path)
        except OSError: pass
    def test_separate_timeframes_and_multiple_open_positions(self):
        self.store.subscribe('BTCUSDT','15m',11,{'risk_usdt':10})
        self.store.subscribe('BTCUSDT','1h',11,{'risk_usdt':20})
        now=int(time.time()*1000)
        a=self.store.open_trade('BTCUSDT','15m','LONG',now,100,95,2,10,{'engine':'RSI_EXTREME_V1'})
        b=self.store.open_trade('BTCUSDT','1h','SHORT',now,100,105,4,20,{'engine':'RSI_EXTREME_V1'})
        self.assertEqual(len(self.store.open_trades('BTCUSDT','15m')),1)
        self.assertEqual(len(self.store.open_trades('BTCUSDT','1h')),1)
        self.assertEqual(len(self.store.all_open()),2)
        self.assertEqual({x['interval'] for x in self.store.configs()},{'15m','1h'})
    def test_disabled_subscription_keeps_monitoring_open_trade(self):
        self.store.subscribe('ETHUSDT','5m',22,{'risk_usdt':5})
        now=int(time.time()*1000)
        self.store.open_trade('ETHUSDT','5m','LONG',now,10,9,5,5,{})
        self.store.unsubscribe('ETHUSDT','5m')
        rows=self.store.configs()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['enabled'],0)


    def test_last_closed_trade_is_available_for_sl_reentry_gate(self):
        self.store.subscribe('ETHUSDT','15m',22,{'risk_usdt':5})
        now=int(time.time()*1000)
        tid=self.store.open_trade('ETHUSDT','15m','LONG',now,100,95,1,5,{})
        self.assertIsNone(self.store.last_closed_trade('ETHUSDT','15m'))
        self.store.close_trade(tid,now+1000,95,'SL',-5)
        last=self.store.last_closed_trade('ETHUSDT','15m')
        self.assertEqual(last['exit_reason'],'SL')
        self.assertEqual(last['id'],tid)

if __name__=='__main__': unittest.main()
