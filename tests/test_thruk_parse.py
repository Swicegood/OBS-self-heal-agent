from obs_self_heal.wrappers.thruk import parse_thruk_stdout


def test_parse_keyword_line() -> None:
    stdout = """login POST -> HTTP 200
tac GET -> HTTP 200
keyword hits (rough): CRITICAL=2 WARNING=1 DOWN=3 UNREACHABLE=0 OK/UP≈10
page title: Thruk
"""
    c, w, d, u, err = parse_thruk_stdout(stdout)
    assert err is None
    assert c == 2 and w == 1 and d == 3 and u == 0


def test_parse_missing() -> None:
    c, w, d, u, err = parse_thruk_stdout("no keywords here")
    assert err == "keyword_line_not_found"
    assert c is None
