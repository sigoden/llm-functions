import urllib.request

def get_ipinfo():
  """
  Get the ip info
  """
  with urllib.request.urlopen("https://httpbin.org/ip") as response:
    data = response.read()
    return data.decode('utf-8')
