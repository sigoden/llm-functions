import sys

sys.path.append("/data/data/com.termux/files/home/.config/aichat/llm-functions/tools")
import bybit_core

params = {
    "category": "linear",
    "symbol": "BTCUSDT",
    "side": "Buy",
    "orderType": "Limit",
    "qty": "0.001",
    "price": "20000",
    "timeInForce": "GTC",
    "positionIdx": 0,
    "slTriggerBy": "Mark",
    "tpTriggerBy": "Mark",
    "stopLoss": "19000",
    "takeProfit": "21000",
}
data1 = bybit_core.api_request("POST", "/v5/order/create", params=params, signed=True)
print("Mark:", data1.get("retMsg"), data1.get("retCode"))

params["slTriggerBy"] = "mark"
params["tpTriggerBy"] = "mark"
data2 = bybit_core.api_request("POST", "/v5/order/create", params=params, signed=True)
print("mark:", data2.get("retMsg"), data2.get("retCode"))

params["slTriggerBy"] = "MarkPrice"
params["tpTriggerBy"] = "MarkPrice"
data3 = bybit_core.api_request("POST", "/v5/order/create", params=params, signed=True)
print("MarkPrice:", data3.get("retMsg"), data3.get("retCode"))
