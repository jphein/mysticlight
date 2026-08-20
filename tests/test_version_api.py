import json
import urllib.request

from mysticlight import version_api


def test_build_version_contract():
    d = version_api.build_version()
    assert d["name"] == "mysticlight"
    assert d["realm"] == "fantasy"
    assert "version" in d and "hash" in d


def test_server_serves_version():
    srv, port = version_api.start_in_thread(0)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version") as r:
            d = json.loads(r.read())
        assert d["name"] == "mysticlight"
    finally:
        srv.shutdown()
