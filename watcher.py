import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag


URL = os.getenv("TARGET_URL", "https://lp.vp4.me/jzze")
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

KNOWN_OOS_TEXT = """הביקוש לערכה היה עצום והמלאי אזל תוך זמן קצר
נעדכן בקרוב על מועד חידוש מלאי הערכות
תודה על ההבנה ❤️""".strip()

FORM_KEYWORDS = [
    "שם",
    "טלפון",
    "נייד",
    "מספר",
    "אימייל",
    "מייל",
    "דוא",
    "שלח",
    "שליחה",
    "קוד",
    "לקבלת קוד",
]

CTA_KEYWORDS = [
    "שלח",
    "שליחה",
    "לקבלת קוד",
    "לקבל קוד",
    "קבל קוד",
]


@dataclass
class CheckResult:
    current_text: str
    page_text: str
    changed: bool
    stock_likely_available: bool
    reason: str
    oos_text_visible: bool
    form_visible: bool
    visible_inputs_count: int
    visible_buttons: List[str]


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = text.replace("״", '"').replace("׳", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "last_text": None,
            "last_status": None,
            "already_notified_for_signature": None,
        }

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "last_text": None,
            "last_status": None,
            "already_notified_for_signature": None,
        }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_page() -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ShilavStockWatcher/2.0; +https://github.com/)"
    }
    response = requests.get(URL, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


def is_hidden_element(element: Tag) -> bool:
    current: Optional[Tag] = element
    while current is not None and isinstance(current, Tag):
        if current.has_attr("hidden"):
            return True

        aria_hidden = str(current.attrs.get("aria-hidden", "")).strip().lower()
        if aria_hidden == "true":
            return True

        classes = " ".join(current.get("class", []))
        if re.search(r"\b(hidden|sr-only|d-none|invisible)\b", classes, flags=re.IGNORECASE):
            return True

        style = str(current.attrs.get("style", "")).replace(" ", "").lower()
        hidden_style_markers = [
            "display:none",
            "visibility:hidden",
            "opacity:0",
            "height:0",
            "max-height:0",
            "width:0",
            "overflow:hidden",
        ]
        if any(marker in style for marker in hidden_style_markers):
            return True

        parent = current.parent
        current = parent if isinstance(parent, Tag) else None

    return False


def element_text(element: Tag) -> str:
    return normalize_text(element.get_text(" ", strip=True))


def find_visible_oos_blocks(soup: BeautifulSoup) -> List[Tag]:
    normalized_known = normalize_text(KNOWN_OOS_TEXT)
    matches: List[Tag] = []

    for element in soup.find_all(["div", "section", "article", "p", "span"]):
        if is_hidden_element(element):
            continue

        text = element_text(element)
        if not text:
            continue

        if normalized_known in text:
            matches.append(element)

    return matches


def visible_inputs_and_buttons(soup: BeautifulSoup) -> Tuple[List[Tag], List[str]]:
    input_like_tags: List[Tag] = []
    button_texts: List[str] = []

    for element in soup.find_all(["input", "select", "textarea", "button"]):
        if is_hidden_element(element):
            continue

        if element.name == "input":
            input_type = str(element.attrs.get("type", "text")).strip().lower()
            if input_type not in {"hidden", "submit", "button", "image", "reset"}:
                input_like_tags.append(element)

        elif element.name in {"select", "textarea"}:
            input_like_tags.append(element)

        elif element.name == "button":
            button_text = element_text(element)
            if button_text:
                button_texts.append(button_text)

    for element in soup.find_all("input"):
        if is_hidden_element(element):
            continue

        input_type = str(element.attrs.get("type", "")).strip().lower()
        value = normalize_text(str(element.attrs.get("value", "")))
        if input_type in {"submit", "button"} and value:
            button_texts.append(value)

    return input_like_tags, button_texts


def count_visible_form_signals(soup: BeautifulSoup) -> Tuple[int, List[str], int]:
    input_like_tags, button_texts = visible_inputs_and_buttons(soup)

    visible_text_chunks: List[str] = []
    for element in soup.find_all(["label", "button", "input", "textarea", "select", "form", "div", "span", "p"]):
        if is_hidden_element(element):
            continue

        text = element_text(element)
        if text:
            visible_text_chunks.append(text)

        for attr_name in ["placeholder", "aria-label", "name", "id", "value"]:
            attr_value = normalize_text(str(element.attrs.get(attr_name, "")))
            if attr_value:
                visible_text_chunks.append(attr_value)

    all_visible_text = " | ".join(visible_text_chunks)

    keyword_hits: List[str] = []
    for keyword in FORM_KEYWORDS:
        if keyword in all_visible_text:
            keyword_hits.append(keyword)

    cta_hits: List[str] = []
    for keyword in CTA_KEYWORDS:
        if any(keyword in button_text for button_text in button_texts):
            cta_hits.append(keyword)

    signal_score = 0
    signal_score += min(len(input_like_tags), 4)
    signal_score += min(len(set(keyword_hits)), 3)
    signal_score += min(len(set(cta_hits)), 2)

    return signal_score, sorted(set(button_texts)), len(input_like_tags)


def extract_relevant_text(soup: BeautifulSoup) -> str:
    visible_chunks: List[str] = []

    for element in soup.find_all(["h1", "h2", "h3", "p", "span", "div", "label", "button"]):
        if is_hidden_element(element):
            continue

        text = element_text(element)
        if text:
            visible_chunks.append(text)

    combined = " | ".join(visible_chunks)

    if "הביקוש לערכה היה עצום" in combined:
        start_index = combined.find("הביקוש לערכה היה עצום")
        snippet = combined[start_index:start_index + 450]
        return normalize_text(snippet)

    return normalize_text(combined[:450])


def page_visible_text(soup: BeautifulSoup) -> str:
    texts: List[str] = []

    for element in soup.find_all(["h1", "h2", "h3", "p", "span", "div", "label", "button"]):
        if is_hidden_element(element):
            continue

        text = element_text(element)
        if text:
            texts.append(text)

    return normalize_text(" ".join(texts))


def detect_change(soup: BeautifulSoup) -> CheckResult:
    current_text = extract_relevant_text(soup)
    visible_page_text = page_visible_text(soup)

    oos_blocks = find_visible_oos_blocks(soup)
    oos_text_visible = len(oos_blocks) > 0

    signal_score, visible_buttons, visible_inputs_count = count_visible_form_signals(soup)
    form_visible = signal_score >= 3

    stock_likely_available = form_visible and not oos_text_visible
    changed = form_visible or not oos_text_visible

    if stock_likely_available:
        reason = "Visible form signals were detected and the out-of-stock message is not visibly present."
    elif form_visible and oos_text_visible:
        reason = "Visible form signals were detected while the out-of-stock message is also still visible."
    elif not oos_text_visible:
        reason = "The out-of-stock message is not visibly present anymore."
    else:
        reason = "The out-of-stock message is still visibly present and no strong visible form signals were detected."

    return CheckResult(
        current_text=current_text,
        page_text=visible_page_text,
        changed=changed,
        stock_likely_available=stock_likely_available,
        reason=reason,
        oos_text_visible=oos_text_visible,
        form_visible=form_visible,
        visible_inputs_count=visible_inputs_count,
        visible_buttons=visible_buttons[:10],
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

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(message)


def build_signature(result: CheckResult) -> str:
    buttons = "|".join(result.visible_buttons)
    return normalize_text(
        f"oos_visible={result.oos_text_visible};"
        f"form_visible={result.form_visible};"
        f"inputs={result.visible_inputs_count};"
        f"buttons={buttons};"
        f"text={result.current_text}"
    )


def maybe_notify(result: CheckResult, state: dict) -> bool:
    signature = build_signature(result)
    last_notified_signature = state.get("already_notified_for_signature")

    if result.changed and signature != last_notified_signature:
        subject = "Shilav coupon page changed"
        body = (
            "The monitored Shilav coupon page looks different.\n\n"
            f"URL: {URL}\n"
            f"Reason: {result.reason}\n"
            f"Visible out-of-stock text: {result.oos_text_visible}\n"
            f"Visible form detected: {result.form_visible}\n"
            f"Visible input count: {result.visible_inputs_count}\n"
            f"Visible buttons: {', '.join(result.visible_buttons) if result.visible_buttons else 'None'}\n\n"
            "Current extracted text:\n"
            f"{result.current_text}\n"
        )
        send_email(subject, body)
        state["already_notified_for_signature"] = signature
        return True

    if not result.changed:
        state["already_notified_for_signature"] = None

    return False


def main() -> int:
    state = load_state()

    try:
        html = fetch_page()
        soup = BeautifulSoup(html, "html.parser")
        result = detect_change(soup)

        notified = maybe_notify(result, state)

        state["last_text"] = result.current_text
        state["last_status"] = "changed" if result.changed else "unchanged"
        state["last_signature"] = build_signature(result)
        state["debug"] = {
            "oos_text_visible": result.oos_text_visible,
            "form_visible": result.form_visible,
            "visible_inputs_count": result.visible_inputs_count,
            "visible_buttons": result.visible_buttons,
            "reason": result.reason,
        }
        save_state(state)

        print(json.dumps({
            "changed": result.changed,
            "stock_likely_available": result.stock_likely_available,
            "reason": result.reason,
            "notified": notified,
            "current_text": result.current_text,
            "oos_text_visible": result.oos_text_visible,
            "form_visible": result.form_visible,
            "visible_inputs_count": result.visible_inputs_count,
            "visible_buttons": result.visible_buttons,
        }, ensure_ascii=False))

        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
