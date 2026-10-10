"""Keep domain assertions readable while checking every inspected wire envelope."""


def response_data(response):
    body = response.json()
    assert isinstance(body, dict), body
    assert len(body) == 2 and body["status_code"] == response.status_code, body
    label = next(key for key in body if key != "status_code")
    value = body[label]
    assert isinstance(value, dict), body
    if response.is_error:
        assert label == "error", body
        assert set(value) == {"code", "message", "details"}, body
        return body
    assert label != "error", body
    if label in {
        "workspaces",
        "credentials",
        "categories",
        "product_history",
        "images",
        "rule_history",
        "matches",
    }:
        assert set(value) == {"items"}, body
        return value["items"]
    return value
