import asyncio
import os
import re
from datetime import datetime, timezone

import requests
from playwright.async_api import async_playwright

ENTRY_URL = "https://bmvs.onlineappointmentscheduling.net.au/oasis/Default.aspx"
LOCATION_URL_PART = "/oasis/Location.aspx"
SESSION_EXPIRED_PART = "/oasis/SessionExpired.aspx"

CITY = os.getenv("CITY", "Alice Springs")
STATE = os.getenv("STATE", "NT")
CHECK_EVERY_SECONDS = max(120, int(os.getenv("CHECK_EVERY_SECONDS", "300")))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
HEADLESS = os.getenv("HEADLESS", "true").lower() not in {"0", "false", "no"}

last_alert_key = None


def log(msg):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{now}] {msg}", flush=True)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log(
            "Telegram NOT configured: "
            f"token={'present' if TELEGRAM_BOT_TOKEN else 'missing'}, "
            f"chat_id={'present' if TELEGRAM_CHAT_ID else 'missing'}"
        )
        return False

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=20,
        )

        if r.ok:
            log("Telegram send OK")
            return True

        try:
            detail = r.json().get("description", "")
        except Exception:
            detail = r.text[:200]
        log(f"Telegram send FAILED: HTTP {r.status_code} {detail}")
        return False

    except Exception as e:
        log(f"Telegram send FAILED: {type(e).__name__}: {e}")
        return False


async def click_new_individual(page):
    candidates = [
        page.get_by_text(re.compile(r"New Individual booking", re.I)),
        page.get_by_role("button", name=re.compile(r"New Individual", re.I)),
        page.get_by_role("link", name=re.compile(r"New Individual", re.I)),
        page.locator('input[type="image"][alt*="Individual" i]'),
        page.locator('img[alt*="Individual" i]'),
    ]

    for loc in candidates:
        try:
            if await loc.count():
                target = loc.first
                tag = await target.evaluate("(el)=>el.tagName")

                if tag == "IMG":
                    clickable = target.locator(
                        "xpath=ancestor::*[self::a or self::button or self::label][1]"
                    )
                    if await clickable.count():
                        await clickable.click()
                    else:
                        await target.click()
                else:
                    await target.click()

                return True
        except Exception:
            pass

    try:
        label = page.locator("text=New Individual booking").first
        if await label.count():
            await label.click()
            return True
    except Exception:
        pass

    return False


async def reach_location_page(page):
    await page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1500)

    body = await page.locator("body").inner_text()
    if re.search(r"captcha|verify you are human|robot", body, re.I):
        return False, "CAPTCHA/human verification detected"

    ok = await click_new_individual(page)
    if not ok:
        return False, "Could not click New Individual booking"

    try:
        await page.wait_for_url(
            re.compile(r".*/oasis/(Location|SessionExpired)\.aspx.*"),
            timeout=30000,
        )
    except Exception:
        await page.wait_for_timeout(3000)

    if SESSION_EXPIRED_PART.lower() in page.url.lower():
        return False, "Session expired immediately"

    if LOCATION_URL_PART.lower() not in page.url.lower():
        return False, f"Unexpected page after New Individual booking: {page.url}"

    return True, "Location page reached"


async def search_city(page):
    city_input = page.get_by_label(
        re.compile(r"City, town, suburb or postcode", re.I)
    )

    if await city_input.count() == 0:
        city_input = page.locator('input[type="text"]:visible').first

    if await city_input.count() == 0:
        return False, "City input not found"

    await city_input.fill(CITY)

    state_select = page.get_by_label(re.compile(r"State", re.I))
    if await state_select.count():
        try:
            await state_select.select_option(label=STATE)
        except Exception:
            try:
                await state_select.select_option(value=STATE)
            except Exception:
                pass

    search_btn = page.get_by_role("button", name=re.compile(r"Search", re.I))
    if await search_btn.count() == 0:
        search_btn = page.locator(
            'input[type="submit"][value*="Search" i], button:has-text("Search")'
        ).first

    if await search_btn.count() == 0:
        return False, "Search button not found"

    await search_btn.click()
    await page.wait_for_timeout(2500)
    return True, "Search complete"


def parse_status(text):
    compact = re.sub(r"\s+", " ", text).strip()
    low = compact.lower()

    idx = low.find("alice springs")
    if idx < 0:
        idx = compact.find("爱丽丝泉")

    if idx < 0:
        return False, None, "Alice Springs result not found"

    chunk = compact[idx : idx + 1400]

    if re.search(
        r"no available slot|no available|没有可用名额|暂无可用|无可用",
        chunk,
        re.I,
    ):
        return False, None, "No Alice Springs appointment currently shown"

    date_patterns = [
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
        r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
        r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s+\d{4})?\b",
    ]

    for pat in date_patterns:
        m = re.search(pat, chunk, re.I)
        if m:
            return True, m.group(0), f"First available date: {m.group(0)}"

    if re.search(r"first available date|最早可用日期", chunk, re.I):
        return True, chunk[:300], (
            "Alice Springs availability may have appeared — check Bupa now"
        )

    return False, None, "Alice Springs found, availability text unclear"


async def check_once(browser):
    context = await browser.new_context(locale="en-AU")
    page = await context.new_page()

    try:
        ok, msg = await reach_location_page(page)
        if not ok:
            body = ""
            try:
                body = await page.locator("body").inner_text()
            except Exception:
                pass

            if re.search(r"captcha|verify you are human|robot", body, re.I):
                return (
                    False,
                    None,
                    "CAPTCHA/human verification detected — manual action required",
                )

            return False, None, msg

        ok, msg = await search_city(page)
        if not ok:
            return False, None, msg

        body = await page.locator("body").inner_text()
        return parse_status(body)

    finally:
        await context.close()


async def main():
    global last_alert_key

    log(
        f"Starting Bupa monitor V2 for {CITY}, "
        f"checking every {CHECK_EVERY_SECONDS}s."
    )

    log(
        "Telegram config: "
        f"token={'present' if TELEGRAM_BOT_TOKEN else 'missing'}, "
        f"chat_id={'present' if TELEGRAM_CHAT_ID else 'missing'}"
    )

    send_telegram(
        "✅ Bupa Monitor is online\n\n"
        f"Location: {CITY}\n"
        f"Checking every {CHECK_EVERY_SECONDS // 60} minutes."
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        while True:
            try:
                available, key, status = await check_once(browser)
                log(status)

                if available:
                    alert_key = key or status

                    if alert_key != last_alert_key:
                        send_telegram(
                            "🚨 BUPA ALICE SPRINGS APPOINTMENT AVAILABLE!\n\n"
                            f"{status}\n\n"
                            "Open the Bupa booking system immediately."
                        )
                        last_alert_key = alert_key
                else:
                    if "CAPTCHA" in status or "manual action" in status.lower():
                        if last_alert_key != "manual":
                            send_telegram(
                                "⚠️ Bupa cloud monitor needs manual attention.\n\n"
                                f"{status}"
                            )
                            last_alert_key = "manual"
                    else:
                        last_alert_key = None

            except Exception as e:
                log(f"Check failed: {type(e).__name__}: {e}")

            await asyncio.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
