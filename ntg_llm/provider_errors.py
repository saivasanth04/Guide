"""Extract allowlisted error reasons; never persist raw provider payloads."""

import json

REASONS = {
    "API_KEY_INVALID": ("The API key is invalid.", "Replace the invalid credential in .env."),
    "API_KEY_EXPIRED": (
        "The API key has expired.",
        "Renew or replace the credential in the provider console.",
    ),
    "API_KEY_LEAKED": (
        "The provider blocked a reported leaked key.",
        "Rotate the exposed key in the provider console and update .env.",
    ),
    "API_KEY_SERVICE_BLOCKED": (
        "Key restrictions block this API.",
        "Allow the intended API in the key service restrictions.",
    ),
    "API_KEY_HTTP_REFERRER_BLOCKED": (
        "The key requires an allowed HTTP referrer.",
        "Use appropriate server-side key restrictions for this Python application.",
    ),
    "API_KEY_IP_ADDRESS_BLOCKED": (
        "The requesting IP is not allowed.",
        "Check the key IP restrictions for this machine.",
    ),
    "SERVICE_DISABLED": (
        "The provider API is disabled for the project.",
        "Enable the intended API in the owning project.",
    ),
    "BILLING_DISABLED": (
        "Billing is disabled for this project.",
        "Review the project billing configuration with the provider.",
    ),
    "CONSUMER_SUSPENDED": (
        "The provider suspended this account or project.",
        "Resolve the suspension with the provider; do not rotate around it.",
    ),
    "ACCESS_TOKEN_SCOPE_INSUFFICIENT": (
        "The credential lacks the required scope.",
        "Review credential scopes and service-account permissions.",
    ),
    "IAM_PERMISSION_DENIED": (
        "The service account lacks the required permission.",
        "Review IAM access for the identity bound to this credential.",
    ),
    "PERMISSION_DENIED": (
        "The provider denied access; the specific reason is unavailable.",
        "Inspect this key and project in the provider console.",
    ),
    "UNAUTHENTICATED": (
        "The provider could not authenticate the credential.",
        "Check this credential in the provider console.",
    ),
}


def access_failure(error, status):
    if status not in (400, 401, 403):
        return None
    payload = None
    try:
        payload = error.response.json()
    except (AttributeError, ValueError, TypeError):
        pass
    raw = str(error)
    if not isinstance(payload, dict):
        position = raw.find("{")
        if position >= 0:
            try:
                payload, _ = json.JSONDecoder().raw_decode(raw[position:])
            except ValueError:
                pass
    body = payload.get("error", payload) if isinstance(payload, dict) else {}
    if not isinstance(body, dict):
        body = {}
    reasons = []
    for detail in body.get("details", []) if isinstance(body.get("details", []), list) else []:
        if (
            isinstance(detail, dict)
            and isinstance(detail.get("reason"), str)
            and detail["reason"] in REASONS
        ):
            reasons.append(detail["reason"])
    text = (raw + " " + str(body.get("message", ""))).lower()
    for fragment, code in (
        ("reported as leaked", "API_KEY_LEAKED"),
        ("api_key_leaked", "API_KEY_LEAKED"),
        ("api key not valid", "API_KEY_INVALID"),
        ("api_key_invalid", "API_KEY_INVALID"),
        ("service_disabled", "SERVICE_DISABLED"),
        ("billing_disabled", "BILLING_DISABLED"),
        ("consumer_suspended", "CONSUMER_SUSPENDED"),
        ("suspended", "CONSUMER_SUSPENDED"),
    ):
        if fragment in text:
            reasons.append(code)
    if status not in (401, 403) and not reasons:
        return None
    code = next(
        (r for r in reasons if r not in ("PERMISSION_DENIED", "UNAUTHENTICATED")),
        "UNAUTHENTICATED" if status == 401 else "PERMISSION_DENIED",
    )
    message, action = REASONS[code]
    return {"reason": code, "explanation": message, "action": action}
