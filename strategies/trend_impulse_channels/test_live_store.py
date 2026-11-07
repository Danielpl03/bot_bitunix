import json
import os
import tempfile
import unittest
from ttr_live import TTRLiveStore
from strategies.trend_impulse_channels.live import TICLiveStore

class TICLiveStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.NamedTemporaryFile(delete=False); self.tmp.close()
        self.db=TTRLiveStore(path=self.tmp.name,backend='sqlite')
        self.store=TICLiveStore(self.db)
    def tearDown(self):
        try: os.unlink(self.tmp.name)
        except OSError: pass
    def test_checkpoint_survives_subscription_update(self):
        self.store.subscribe('ETHUSDT','1h',123,{'tp1_pct':40,'risk_usdt':10,'sl_candles':5})
        self.store.checkpoint('ETHUSDT','1h',123456,{'version':1,'recent_bars':[]},{'trend':100},[])
        self.store.subscribe('ETHUSDT','1h',123,{'tp1_pct':60,'risk_usdt':12,'sl_candles':3})
        row=self.store.get_config('ETHUSDT','1h')
        self.assertEqual(row['last_bar_time'],123456)
        self.assertEqual(json.loads(row['indicator_state'])['version'],1)
        self.assertEqual(json.loads(row['config'])['tp1_pct'],60)
    def test_stop_keeps_open_trade_and_config_history(self):
        self.store.subscribe('BTCUSDT','4h',123,{'tp1_pct':50})
        self.store.open_trade('BTCUSDT','4h','LONG',1000,100,95,2,50,2)
        self.store.unsubscribe('BTCUSDT','4h')
        self.assertEqual(len(self.store.open_trades('BTCUSDT','4h')),1)
        self.assertEqual(self.store.configs()[0]['enabled'],0)
    def test_stats_are_grouped_by_symbol(self):
        self.store.subscribe('BTCUSDT','1h',123,{'tp1_pct':50})
        self.store.open_trade('BTCUSDT','1h','LONG',1000,100,95,2,50,2)
        stats=self.store.stats()
        self.assertEqual(stats['overall']['open'],1)
        self.assertIn('BTCUSDT',stats['by_symbol'])

if __name__=='__main__': unittest.main()
