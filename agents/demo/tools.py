import urllib.request


def get_ipinfo():
    """
    Get the ip info
    """
    with urllib.request.urlopen("https://api.ipify.org") as response:
        data = response.read()
        return data.decode("utf-8")
