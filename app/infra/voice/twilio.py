from html import escape


def build_twiml_gather_response(*, message: str, action_url: str) -> str:
    safe_message = escape(message)
    safe_action = escape(action_url)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Say>{safe_message}</Say>"
        f'<Gather input="speech" action="{safe_action}" method="POST" speechTimeout="auto" />'
        "</Response>"
    )

