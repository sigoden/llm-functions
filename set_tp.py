from tools.bybit_terminal import BybitRealm

realm = BybitRealm()
print(realm.set_trading_stop(symbol="LINKUSDT", take_profit=9.25, stop_loss=9.10))
