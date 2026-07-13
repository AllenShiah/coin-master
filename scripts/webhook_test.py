"""End-to-end webhook tester.

Builds LINE webhook payloads, signs them exactly like the LINE platform would
(HMAC-SHA256 over the raw body with the channel secret), and POSTs them to the
running FastAPI app with ``curl``.  Meant to be run *inside* the container:

    docker exec <container> python /app/scripts/webhook_test.py

The app replies are echoed in the JSON response body (LINE itself ignores the
body); we assert on them here.  Assumes the in-memory store and today ~2026/07/12.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys

URL = os.environ.get("TEST_URL", "http://localhost:8000/line-bot")
SECRET = os.environ["LINE_CHANNEL_SECRET"].encode("utf-8")

BOB = "Ubob0000000000000000000000000001"
ALICE = "Ualice00000000000000000000000002"
CAROL = "Ucarol00000000000000000000000003"
DAVE = "Udave000000000000000000000000004"
EMMA = "Uemma000000000000000000000000005"
FRANK = "Ufrank00000000000000000000000006"
GRACE = "Ugrace00000000000000000000000007"
HENRY = "Uhenry00000000000000000000000008"

_passed = 0
_failed = 0


def sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(SECRET, body, hashlib.sha256).digest()).decode()


def post(event: dict) -> dict:
    body = json.dumps({"destination": "Uboot", "events": [event]}, ensure_ascii=False).encode("utf-8")
    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST", URL,
            "-H", f"x-line-signature: {sign(body)}",
            "-H", "content-type: application/json",
            "--data-binary", "@-",
        ],
        input=body,
        capture_output=True,
    )
    return json.loads(result.stdout.decode("utf-8"))


def mention(text: str, display: str, user_id: str) -> dict:
    token = "@" + display
    return {
        "index": text.index(token),
        "length": len(token),
        "userId": user_id,
        "type": "user",
        "isSelf": False,
    }


def event(user_id: str, text: str, event_id: str, mentionees=None, ts=1747000000000) -> dict:
    message: dict = {"type": "text", "id": "msg-" + event_id, "text": text}
    if mentionees:
        message["mention"] = {"mentionees": mentionees}
    return {
        "type": "message",
        "mode": "active",
        "webhookEventId": event_id,
        "replyToken": "reply-" + event_id,
        "timestamp": ts,
        "source": {"type": "group", "groupId": "Groom", "userId": user_id},
        "message": message,
    }


def register(debtor: str, creditor: str, creditor_name: str, body_text: str, event_id: str) -> dict:
    text = f"債權人: @{creditor_name} {body_text}"
    return event(debtor, text, event_id, mentionees=[mention(text, creditor_name, creditor)])


def check(user: str, event_id: str, arg: str = "") -> dict:
    text = "check" + (f" {arg}" if arg else "")
    return event(user, text, event_id)


def expect(label: str, reply: str, *needles: str) -> None:
    global _passed, _failed
    missing = [n for n in needles if n not in (reply or "")]
    if missing:
        _failed += 1
        print(f"  ✗ {label}\n    missing {missing}\n    got: {reply!r}")
    else:
        _passed += 1
        print(f"  ✓ {label}")


def first_reply(response: dict) -> str:
    replies = response.get("replies", [])
    return replies[0] if replies else ""


def main() -> int:
    print("== Scenario A: Bob borrows 100,000 from Alice on 2026/05/10 @2.92% ==")
    r = post(register(BOB, ALICE, "Alice", "日期: 2026/05/10 借款: 100,000 年利率: 2.92%", "A1"))
    expect("register borrow", first_reply(r), "已記錄", "借款", "100,000.00", "2.92%")

    print("== Check Bob (all months, as debtor) ==")
    r = post(check(BOB, "A2"))
    reply = first_reply(r)
    print(reply)
    expect(
        "bob debtor totals + monthly interest",
        reply,
        "目前欠款總額：100,000.00 元",
        "2026年05月：88.00 元",
        "2026年06月：248.00 元",
        "2026年07月：176.00 元",
        "尚未結算",
    )

    print("== Check Bob for 2026/06 only ==")
    r = post(check(BOB, "A3", "2026/06"))
    expect("bob single month june", first_reply(r), "查詢月份：2026年06月", "應付利息：248.00 元")

    print("== Check Alice (as creditor) ==")
    r = post(check(ALICE, "A4"))
    reply = first_reply(r)
    print(reply)
    expect(
        "alice creditor view",
        reply,
        "您是債權人",
        "目前應收總額：100,000.00 元",
        "2026年06月：248.00 元",
    )

    print("== Scenario B: idempotency (resend A1) ==")
    r = post(register(BOB, ALICE, "Alice", "日期: 2026/05/10 借款: 100,000 年利率: 2.92%", "A1"))
    expect("duplicate delivery produces no reply", "" if not r.get("replies") else "x", "")
    r = post(check(BOB, "B1"))
    expect("bob balance unchanged after dup", first_reply(r), "目前欠款總額：100,000.00 元")

    print("== Scenario C: format error (missing 年利率) ==")
    text = "債權人: @Alice 日期: 2026/05/10 借款: 5000"
    r = post(event(BOB, text, "C1", mentionees=[mention(text, "Alice", ALICE)]))
    expect("format error reply", first_reply(r), "格式不符", "年利率", "範例")

    print("== Scenario D: same-day borrow+repay nets to zero interest ==")
    post(register(CAROL, DAVE, "Dave", "日期: 2026/07/12 借款: 100,000 年利率: 2.92%", "D1"))
    post(register(CAROL, DAVE, "Dave", "日期: 2026/07/12 還款: 100,000 年利率: 2.92%", "D2"))
    r = post(check(CAROL, "D3"))
    reply = first_reply(r)
    print(reply)
    expect(
        "same-day nets to zero",
        reply,
        "目前欠款總額：0.00 元",
        "2026年07月：0.00 元",
    )

    print("== Scenario F: mid-window repay lowers that day's interest (spec example) ==")
    # Emma borrows 10,000 on 05/10, repays 5,000 on 05/12 @2.92%.
    # 05/10-05/11 @10,000 (2d) + 05/12-05/20 @5,000 (9d) = 2*0.8 + 9*0.4 = 5.20
    post(register(EMMA, FRANK, "Frank", "日期: 2026/05/10 借款: 10,000 年利率: 2.92%", "F1"))
    post(register(EMMA, FRANK, "Frank", "日期: 2026/05/12 還款: 5,000 年利率: 2.92%", "F2"))
    r = post(check(EMMA, "F3", "2026/05"))
    reply = first_reply(r)
    print(reply)
    expect("mid-window repay", reply, "目前欠款總額：5,000.00 元", "應付利息：5.20 元")

    print("== Scenario G: different rates are summed separately, never blended ==")
    # Grace borrows 100,000 @2.92% and 100,000 @3.65% on 05/10. May window 04/21-05/20.
    # 2.92%: 11d * 8 = 88 ; 3.65%: 11d * 10 = 110 ; total 198.00 (not a blended rate)
    post(register(GRACE, HENRY, "Henry", "日期: 2026/05/10 借款: 100,000 年利率: 2.92%", "G1"))
    post(register(GRACE, HENRY, "Henry", "日期: 2026/05/10 借款: 100,000 年利率: 3.65%", "G2"))
    r = post(check(GRACE, "G3", "2026/05"))
    reply = first_reply(r)
    print(reply)
    expect("multi-rate summed separately", reply, "目前欠款總額：200,000.00 元", "應付利息：198.00 元")

    print("== Scenario E: bad signature is rejected ==")
    body = json.dumps({"events": []}).encode()
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", URL,
         "-H", "x-line-signature: deadbeef", "--data-binary", "@-"],
        input=body, capture_output=True,
    )
    code = result.stdout.decode()
    expect("403 on bad signature", "ok" if code == "403" else f"code={code}", "ok")

    print(f"\n==== {_passed} passed, {_failed} failed ====")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
