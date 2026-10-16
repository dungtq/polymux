from pm.security import useragent

CHROME_LINUX = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
SAFARI_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                 "Version/18.6 Mobile/15E148 Safari/604.1")
SAFARI_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "Version/18.6 Safari/605.1.15")
EDGE_WIN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
            "Safari/537.36 Edg/141.0.0.0")
FIREFOX_ANDROID = "Mozilla/5.0 (Android 15; Mobile; rv:143.0) Gecko/143.0 Firefox/143.0"
CHROME_IOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "CriOS/141.0 Mobile/15E148 Safari/604.1")


def test_family_browser_and_os():
    assert useragent.family(CHROME_LINUX) == "Chrome · Linux"
    assert useragent.family(SAFARI_IPHONE) == "Safari · iPhone"
    assert useragent.family(SAFARI_MAC) == "Safari · macOS"
    assert useragent.family(EDGE_WIN) == "Edge · Windows"
    assert useragent.family(FIREFOX_ANDROID) == "Firefox · Android"
    assert useragent.family(CHROME_IOS) == "Chrome · iPhone"


def test_family_partial_and_empty():
    assert useragent.family("curl/8.5.0") == "curl"
    assert useragent.family("SomeBot (Linux)") == "Linux"
    assert useragent.family("") == "" and useragent.family(None) == "" and useragent.family("x" * 5000) == ""
