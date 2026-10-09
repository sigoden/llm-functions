def run(action, **kwargs):
    realm = get_realm()
    logger.info(f"Executing Action: {action} | Params: {kwargs}")
    try:
        if action == "pnl":
            return calculate_tp_pnl(
                kwargs.get("entry"),
                kwargs.get("tp"),
                kwargs.get("qty"),
                kwargs.get("side"),
                leverage=kwargs.get("leverage", 1.0),
            )
        elif action == "signal":
            return analyze_trade_signal(kwargs.get("symbol"))
        elif action == "depth":
            return get_market_depth_analysis(kwargs.get("symbol"))
        elif hasattr(realm, action):
            return getattr(realm, action)(**kwargs)
        else:
            return {"status": "error", "msg": f"Unknown action: {action}"}
    except Exception as exc:
        logger.error(f"run({action}) raised: {exc}")
        return {"status": "error", "msg": str(exc)}
