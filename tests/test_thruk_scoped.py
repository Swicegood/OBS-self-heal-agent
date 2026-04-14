"""Scoped Thruk row parsing (no network)."""

from obs_self_heal.wrappers.thruk_scoped import _count_in_rows, _extract_service_status_from_html, _extract_service_status_from_status_html, count_scoped_status_keywords


def test_scoped_row_counts_only_matching_row() -> None:
    html = """
    <html><table>
    <tr><td>Other Host</td><td>Other Service</td><td>CRITICAL</td></tr>
    <tr><td>Mayapur TV</td><td>Hillsborough Flash Stream</td><td>OK</td></tr>
    <tr><td>Elsewhere</td><td>Downstream</td><td>DOWN</td></tr>
    </table></html>
    """
    c, w, d, u, err = _count_in_rows(
        html,
        "Hillsborough Flash Stream",
        ["Mayapur TV", "broadcast1.mayapur.tv"],
    )
    assert err is None
    assert c == 0 and w == 0 and d == 0 and u == 0


def test_scoped_row_picks_critical_in_matching_row() -> None:
    html = """
    <tr><td>Mayapur TV (broadcast1.mayapur.tv)</td><td>Hillsborough Flash Stream</td><td>CRITICAL</td></tr>
    """
    c, w, d, u, err = _count_in_rows(
        html,
        "Hillsborough Flash Stream",
        ["Mayapur TV"],
    )
    assert err is None
    assert c == 1 and d == 0


def test_scoped_row_ignores_critical_elsewhere() -> None:
    html = """
    <tr><td>Other</td><td>Other Svc</td><td>CRITICAL</td></tr>
    <tr><td>Mayapur TV</td><td>Hillsborough Flash Stream</td><td>OK</td></tr>
    """
    c, w, d, u, err = _count_in_rows(
        html,
        "Hillsborough Flash Stream",
        ["Mayapur TV"],
    )
    assert err is None
    assert c == 0


def test_scoped_row_not_found() -> None:
    html = "<tr><td>x</td></tr>"
    c, w, d, u, err = _count_in_rows(html, "Hillsborough Flash Stream", ["Mayapur TV"])
    assert err == "scoped_service_row_not_found"
    assert c is None


def test_proximity_finds_host_and_service_in_separate_tr() -> None:
    """Thruk often places host and service in different `<tr>` cells."""
    html = """
    <table>
    <tr><td class="host">Mayapur TV</td><td>broadcast1.mayapur.tv</td></tr>
    <tr><td colspan="2">Hillsborough Flash Stream</td><td>UP</td></tr>
    </table>
    """
    c, w, d, u, err = count_scoped_status_keywords(
        html,
        "Hillsborough Flash Stream",
        ["Mayapur TV", "broadcast1.mayapur.tv"],
        4500,
    )
    assert err is None
    assert c == 0 and d == 0


def test_proximity_counts_critical_in_shared_window() -> None:
    html = """
    <div>Mayapur TV</div>
    <div>Hillsborough Flash Stream</div>
    <span>CRITICAL</span>
    """
    c, w, d, u, err = count_scoped_status_keywords(
        html,
        "Hillsborough Flash Stream",
        ["Mayapur TV"],
        4500,
    )
    assert err is None
    assert c == 1


def test_extract_service_status_from_extinfo_visible_text() -> None:
    html = """
    <html>
      <body>
        <h2>Service: Hillsborough Flash Stream</h2>
        <div>Current Status: OK</div>
      </body>
    </html>
    """
    st, err = _extract_service_status_from_html(html)
    assert err is None
    assert st == "OK"


def test_extract_service_status_from_status_cgi_visible_text() -> None:
    html = """
    <html>
      <body>
        <h1>Status</h1>
        <table>
          <tr><td>Hillsborough Flash Stream</td><td>OK</td></tr>
          <tr><td>Other</td><td>CRITICAL</td></tr>
        </table>
      </body>
    </html>
    """
    st, err = _extract_service_status_from_status_html(html, "Hillsborough Flash Stream")
    assert err is None
    assert st == "OK"
