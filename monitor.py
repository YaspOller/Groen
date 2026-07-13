#!/usr/bin/env python3
import os
import re
import sys
import time
import json
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

TARGET_URL = "https://groenkoncert.dk/billetter/" 
CITY = "Aarhus"
ALL_CITIES = ["Tårnby", "Kolding", "Aarhus", "Aalborg", "Esbjerg", "Odense", "Næstved", "Valby"]

STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "state.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
RUN_DURATION_SECONDS = 260
CHECK_INTERVAL_SECONDS = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
}

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def fetch_page() -> str:
    resp = requests.get(TARGET_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()

def extract_city_block(html: str, city: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text("\n")
    lines = [l.strip() for l in full_text.split("\n") if l.strip()]

    try:
        start_marker = lines.index("Koncerter Billeter")
    except ValueError:
        start_marker = 0

    section = lines[start_marker:]

    try:
        city_start = section.index(city)
    except ValueError:
        raise RuntimeError(f"Kunne ikke finde byen '{city}' på siden.")

    city_end = len(section)
    for other_city in ALL_CITIES:
        if other_city == city:
            continue
        for idx in range(city_start + 1, len(section)):
            if section[idx] == other_city:
                city_end = min(city_end, idx)
                break

    return "\n".join(section[city_start:city_end])

def check_tickets_status(city_block_text: str) -> dict:
    full_text = normalize(city_block_text)
    no_tickets_phrase = "ingen resalebilletter tilgængeligt pt"
    
    if no_tickets_phrase in full_text:
        return {"available": False, "reason": f"Ingen billetter ('ingen resalebilletter tilgængeligt pt' fundet for {CITY})."}
    
    if "billet ledig" in full_text or "billetter ledig" in full_text or re.search(r"\b[1-9]\d*\s*billet", full_text):
        return {"available": True, "reason": "Positiv tekst fundet (fx 'billet ledig')."}
        
    return {"available": True, "reason": f"Teksten 'ingen resalebilletter tilgængeligt pt.' er forsvundet for {CITY}!"}

def send_discord_message(content: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        log("Mangler DISCORD_WEBHOOK_URL!")
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=15)
    except Exception as e:
        log(f"Fejl ved Discord: {e}")

def check_once(state: dict) -> dict:
    try:
        html = fetch_page()
    except Exception as e:
        log(f"Fejl: {e}")
        return state

    try:
        city_block = extract_city_block(html, CITY)
    except RuntimeError as e:
        log(str(e))
        return state

    status = check_tickets_status(city_block)
    currently_available = status["available"]
    reason = status["reason"]
    previous_available = state.get("available")

    if previous_available is None:
        log(f"Første kørsel - gemmer status: {reason}")
        state["available"] = currently_available
        return state

    if currently_available and not previous_available:
        msg = f"🎟️ **BILLETTER TILGÆNGELIGE TIL {CITY.upper()}!** 🎟️\n**Årsag:** {reason}\nKøb her: {TARGET_URL}"
        send_discord_message(msg)
    elif not currently_available and previous_available:
        send_discord_message(f"ℹ️ Billetter til **{CITY}** er væk igen. ({reason})")

    state["available"] = currently_available
    return state

def main():
    state = load_state()
    start_time = time.monotonic()
    
    while True:
        state = check_once(state)
        save_state(state)
        if (time.monotonic() - start_time) + CHECK_INTERVAL_SECONDS >= RUN_DURATION_SECONDS:
            break
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
