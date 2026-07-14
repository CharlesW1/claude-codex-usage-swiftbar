import urllib.request

def block_network(*args, **kwargs):
    raise RuntimeError("Network access is blocked in tests")

urllib.request.urlopen = block_network
