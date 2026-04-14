from obs_self_heal.wrappers.obs_ws_client import _auth_string


def test_auth_string_deterministic() -> None:
    """Protocol: SHA256(password+salt) -> b64; SHA256(b64+challenge) -> b64 (doc fragments may not cross-match)."""

    salt = "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI="
    challenge = "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="
    auth = _auth_string("supersecretpassword", salt, challenge)
    assert auth == "1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4="
