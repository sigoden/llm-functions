def run(
    action,
    symbol=None,
    side=None,
    qty=None,
    price=None,
    category="linear",
    order_id=None,
    stop_loss=None,
    take_profit=None,
    reduce_only=False,
    time_in_force="GTC",
    position_idx=0,
    leverage=None,
    account_type="UNIFIED",
    limit=25,
    interval="1",
    interval_time="5min",
    sl_pct=None,
    tp_pct=None,
    risk_usdt=None,
    sl_price=None,
    orders=None,
    slices=5,
    delay=None,
    start_time=None,
    end_time=None,
    **kwargs,
):
    bot = _get_dispatcher()
    try:
        # Initial checks/prep
        cat = (
            Category(str(category).strip())
            if category and str(category).strip()
            else Category.LINEAR
        )

        if action == "health_check":
            return bot.health_check()
        elif action == "place_order":
            # ... (place order logic)
            return {"status": "ok"}
        elif action == "get_ticker":
            return bot.get_ticker(symbol=symbol, category=cat)
        # ... (all other elifs)
        else:
            return {"status": "error", "msg": f"Unknown action: {action}"}
    except Exception as exc:
        logger.exception("run() error: %s", exc)
        return {"status": "error", "msg": str(exc)}
