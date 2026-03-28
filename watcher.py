import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup


URL = os.getenv("TARGET_URL", "https://lp.vp4.me/jzze")
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

KNOWN_OOS_TEXT = """הביקוש לערכה היה עצום והמלאי אזל תוך זמן קצר
נעדכן בקרוב על מועד חידוש מלאי הערכות
תודה על ההבנה ❤️""".strip()


@dataclass
class CheckResult:
    current_text: str
    page_text: str
    changed: bool
    stock_likely_available: bool
    reason: str


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "last_text": None,
            "last_status": None,
            "already_notified_for_text": None,
        }

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "last_text": None,
            "last_status": None,
            "already_notified_for_text": None,
        }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_page() -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ShilavStockWatcher/1.0; +https://github.com/)"
    }
    response = requests.get(URL, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


def extract_relevant_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    texts = []
    for element in soup.find_all(["div", "p", "span", "section", "article", "form", "label", "h1", "h2", "h3", "input", "button"]):
        text = element.get_text(" ", strip=True)
        if text:
            texts.append(text)

    combined = "\n".join(texts)

    if "הביקוש לערכה היה עצום" in combined:
        start_index = combined.find("הביקוש לערכה היה עצום")
        snippet = combined[start_index:start_index + 300]
        return normalize_text(snippet)

    return normalize_text(combined)


def detect_change(current_text: str, full_page_text: str) -> CheckResult:
    normalized_known = normalize_text(KNOWN_OOS_TEXT)
    normalized_current = normalize_text(current_text)
    normalized_page = normalize_text(full_page_text)

    changed = normalized_known not in normalized_page

    stock_likely_available = changed

    if changed:
        reason = "The known out-of-stock text is no longer present on the page."
    else:
        reason = "The known out-of-stock text is still present on the page."

    return CheckResult(
        current_text=normalized_current,
        page_text=normalized_page,
        changed=changed,
        stock_likely_available=stock_likely_available,
        reason=reason,
    )


def send_email(subject: str, body: str) -> None:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_from = os.getenv("EMAIL_FROM", smtp_user or "")
    email_to = os.getenv("EMAIL_TO")

    if not all([smtp_host, smtp_user, smtp_password, email_to]):
        raise RuntimeError("Missing SMTP configuration in environment variables.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = email_to
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(message)


def maybe_notify(result: CheckResult, state: dict) -> bool:
    last_notified_text = state.get("already_notified_for_text")

    if result.changed and result.current_text != last_notified_text:
        subject = "Shilav coupon page changed"
        body = (
            "The monitored section on the Shilav coupon page changed.\n\n"
            f"URL: {URL}\n"
            f"Reason: {result.reason}\n\n"
            "Current extracted text:\n"
            f"{result.current_text}\n"
        )
        send_email(subject, body)
        state["already_notified_for_text"] = result.current_text
        return True

    if not result.changed:
        state["already_notified_for_text"] = None

    return False


def main() -> int:
    state = load_state()

    try:
        html = fetch_page()
        current_text = extract_relevant_text(html)
        page_text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        result = detect_change(current_text=current_text, full_page_text=page_text)

        notified = maybe_notify(result, state)

        state["last_text"] = result.current_text
        state["last_status"] = "changed" if result.changed else "unchanged"
        save_state(state)

        print(json.dumps({
            "changed": result.changed,
            "stock_likely_available": result.stock_likely_available,
            "reason": result.reason,
            "notified": notified,
            "current_text": result.current_text,
        }, ensure_ascii=False))

        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
