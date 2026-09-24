
Bupa Alice Springs Cloud Monitor
================================

What it does
------------
- Runs a headless Chromium browser in the cloud.
- Starts at Bupa's Default.aspx page.
- Clicks "New Individual booking".
- Goes to the Location page.
- Searches Alice Springs.
- Checks "First Available Date".
- Sends a Telegram message when a slot/date appears.
- Starts a fresh booking session on every check, so it does not rely on one long-lived Bupa session.
- It does NOT book the appointment.

Files
-----
monitor.py
Dockerfile
requirements.txt
railway.json

Telegram setup
--------------
You need:
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID

Keep the bot token private.

Recommended environment variables
---------------------------------
TELEGRAM_BOT_TOKEN = your Telegram bot token
TELEGRAM_CHAT_ID = your Telegram chat ID
CHECK_EVERY_SECONDS = 300
CITY = Alice Springs
STATE = NT
HEADLESS = true

The code enforces a minimum interval of 120 seconds. 300 seconds (5 minutes) is recommended.

Railway deployment (simple option)
----------------------------------
1. Create a new Railway project.
2. Deploy this folder/repository using the Dockerfile.
3. Add the environment variables above.
4. Deploy.
5. Watch the service logs. You should see lines such as:
   "No Alice Springs appointment currently shown"
6. Keep the service running continuously.

Important
---------
- Bupa may change its page structure; selectors may need updating later.
- If Bupa introduces CAPTCHA/human verification, this monitor does not bypass it.
  It will instead send a Telegram warning that manual action is required.
- Cloud providers may charge for continuous browser workloads.
- Check Bupa's website terms before running automated monitoring.
- Never put your HAP ID or passport number in this code. This monitor only checks the
  location/availability stage and does not need those details based on the current flow.
