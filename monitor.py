#!/usr/bin/env python3
import os
import re
import sys
import time
import json
from datetime import datetime, timezone
import requests
from playwright.sync_api import sync_playwright

CITY = "Aarhus"
TARGET_URL = "https://groenkoncert.dk/billetter/" 

STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "state.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

RUN_DURATION_SECONDS = 260
CHECK_INTERVAL_SECONDS = 30 

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def send_discord_message(content: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        log("Mangler DISCORD_WEBHOOK_URL!")
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=15)
    except Exception as e:
        log(f"Fejl ved Discord: {e}")

def check_once(state: dict, context) -> dict:
    page = context.new_page()
    try:
        log(f"Åbner Grøn Koncert for at tjekke {CITY}...")
        page.goto(TARGET_URL, wait_until="networkidle", timeout=30000)
        
        # 1. AGGRESSIVT KLIK PÅ COOKIE-BANNER
        # Vi leder efter alle knapper der kunne være accept-cookies
        cookie_buttons = page.locator("button, a")
        for i in range(cookie_buttons.count()):
            btn = cookie_buttons.nth(i)
            try:
                txt = btn.inner_text().lower()
                if any(word in txt for word in ["accept", "tillad", "okay", "godkend"]):
                    btn.click(timeout=1000)
                    log("Cookie-banner fjernet!")
                    page.wait_for_timeout(1000) # Vent lige lidt efter klik
                    break
            except:
                continue
            
        # 2. Find Aarhus-knappen og klik
        clicked = page.evaluate('''() => {
            let links = Array.from(document.querySelectorAll("a, button"));
            for (let link of links) {
                let txt = (link.innerText || "").toLowerCase();
                if (txt.includes("køb") || txt.includes("resale") || txt.includes("venteliste")) {
                    let parent = link.parentElement;
                    for(let i=0; i<8; i++) {
                        if (parent && parent.innerText && parent.innerText.includes("Aarhus") && !parent.innerText.includes("Aalborg")) {
                            link.click();
                            return true;
                        }
                        parent = parent.parentElement;
                    }
                }
            }
            return false;
        }''')
        
        if not clicked:
            log("Kunne ikke klikke på knappen!")
        else:
            log("Klikket! Venter 10 sek. på at billetsystemet loader ind...")
        
        page.wait_for_timeout(10000) 
        
        # 3. Læs ALT tekst på siden igen
        full_text = page.inner_text("body").lower()
        # Tjek også frames/popups specifikt
        for frame in page.frames:
            try:
                full_text += " " + frame.inner_text("body").lower()
            except:
                pass
                
        full_text = re.sub(r"\s+", " ", full_text).strip()
        
    except Exception as e:
        log(f"Fejl: {e}")
        page.close()
        return state
        
    page.close()
    
    # 4. Analysér
    no_tickets_phrase = "ingen resalebilletter tilgængeligt pt"
    
    # Her tjekker vi for "ingen" ELLER "udsolgt" som sikkerhed
    if no_tickets_phrase in full_text or "udsolgt" in full_text:
        currently_available = False
        reason = "Udsolgt-tekst fundet."
    elif "billet ledig" in full_text or "billetter ledig" in full_text or re.search(r"\b[1-9]\d*\s*billet", full_text):
        currently_available = True
        reason = "Positiv tekst fundet (fx 'billet ledig')."
    else:
        # Hvis vi IKKE finder udsolgt-teksten, og heller ikke "billet ledig", 
        # er det stadig usikkert - vi gemmer uddraget til dig.
        currently_available = False 
        log(f"-> Robotten ser stadig dette (uddrag): {full_text[:300]}...")
        reason = "Systemet viser hverken 'udsolgt' eller 'ledig' - mulig fejl."
        
    previous_available = state.get("available")
    
    if previous_available is None:
        state["available"] = currently_available
        return state

    if currently_available and not previous_available:
        send_discord_message(f"🎟️ **BILLETTER TIL {CITY.upper()}!**\nSkynd dig: {TARGET_URL}")
    elif not currently_available and previous_available:
        send_discord_message(f"ℹ️ Billetter til **{CITY}** er væk/ikke fundet.")
    else:
        log(f"Status uændret: {reason}")
        
    state["available"] = currently_available
    return state

def main():
    state = load_state()
    start_time = time.monotonic()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0", locale="da-DK")
        
        while True:
            state = check_once(state, context)
            save_state(state)
            if (time.monotonic() - start_time) + CHECK_INTERVAL_SECONDS >= RUN_DURATION_SECONDS:
                break
            time.sleep(CHECK_INTERVAL_SECONDS)
        browser.close()

if __name__ == "__main__":
    main()
