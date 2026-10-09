with open("tools/bybit_wbta.py") as f:
    code = f.read()

code = code.replace(
    '        # Dynamic Endpoint Failover / DNS check\\n        base_url = "https://api.bybit.com"\\n        try:\\n            import socket\\n            # Attempt to resolve main domain\\n            socket.gethostbyname("api.bybit.com")\\n        except Exception:\\n            # Fall back to backup endpoint if name resolution fails\\n            base_url = "https://api.bytick.com"',
    '        base_url = "https://api.bybit.com"\\n        if not self.use_tor:\\n            try:\\n                import socket\\n                socket.gethostbyname("api.bybit.com")\\n            except Exception:\\n                base_url = "https://api.bytick.com"',
)

with open("tools/bybit_wbta.py", "w") as f:
    f.write(code)
