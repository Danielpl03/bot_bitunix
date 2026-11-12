import unittest
from unittest.mock import Mock
from strategies.trend_impulse_channels.live import fetch_closed_candles

class TestClosedCandleFetch(unittest.TestCase):
    def test_fetches_latest_without_end_time_and_filters_forming_candle(self):
        width=3_600_000
        now=1_794_067_200_000 + 26*60_000  # 00:26 UTC, 1h candle 00:00 still forming
        expected=(now//width)*width-width
        rows=[
            {'time':expected-width,'open':1},
            {'time':expected,'open':2},
            {'time':expected+width,'open':3},
        ]
        client=Mock();client.get_kline_history.return_value=rows
        result=fetch_closed_candles(client,'BTCUSDT','1h',total=1000,now_ms=now,start_time=expected-width+1)
        client.get_kline_history.assert_called_once_with('BTCUSDT','1h',total=1000,start_time=expected-width+1)
        self.assertEqual([x['time'] for x in result],[expected-width,expected])


    def test_incremental_history_uses_forward_pagination_not_backward_warmup(self):
        from bitunix_client import BitunixClient
        client=BitunixClient()
        calls=[]
        def fake_get_kline(symbol, interval, limit=100, start_time=None, end_time=None, kline_type='LAST_PRICE'):
            calls.append((limit,start_time,end_time))
            if len(calls)==1:
                return [{'time':1000+i,'close':i} for i in range(200)]
            return [{'time':1200,'close':201}]
        client.get_kline=fake_get_kline
        rows=client.get_kline_history('BTCUSDT','1h',total=10000,start_time=1000)
        self.assertEqual(len(rows),201)
        self.assertEqual(rows[-1]['time'],1200)
        self.assertEqual(calls[0][0],200)
        self.assertEqual(calls[1][1],1200)

    def test_incremental_history_stops_when_api_returns_no_new_rows(self):
        from bitunix_client import BitunixClient
        client=BitunixClient(); calls=[]
        def fake_get_kline(*args,**kwargs):
            calls.append(kwargs.get('start_time'))
            return [{'time':1000,'close':1}]
        client.get_kline=fake_get_kline
        rows=client.get_kline_history('BTCUSDT','1h',total=10000,start_time=1000)
        self.assertEqual(len(rows),1)
        self.assertEqual(len(calls),1)

    def test_no_start_time_for_warmup(self):
        width=3_600_000
        now=1_794_067_200_000 + 26*60_000
        expected=(now//width)*width-width
        client=Mock();client.get_kline_history.return_value=[{'time':expected}]
        result=fetch_closed_candles(client,'ETHUSDT','1h',total=10000,now_ms=now)
        client.get_kline_history.assert_called_once_with('ETHUSDT','1h',total=10000,start_time=None)
        self.assertEqual(result,[{'time':expected}])

if __name__=='__main__': unittest.main()
