#!/usr/bin/env python3
import os
import re
import sys
import time
import json
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup
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
        log(f"Åbner browser og tjekker {CITY}...")
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        
        # 1. Klik cookie-boks væk
        try:
            page.locator("button, a", has_text=re.compile(r"accepter|tillad", re.I)).first.click(timeout=2000)
        except:
            pass
            
        # 2. Finder og KLIKKER på knappen
        clicked = page.evaluate('''() => {
            let links = Array.from(document.querySelectorAll("a, button"));
            for (let link of links) {
                let txt = (link.innerText || "").toLowerCase();
                if (txt.includes("køb") || txt.includes("resale") || txt.includes("venteliste")) {
                    let parent = link.parentElement;
                    for(let i=0; i<8; i++) {
                        if (parent && parent.innerText) {
                            let pText = parent.innerText;
                            if (pText.includes("Aarhus") && !pText.includes("Aalborg") && !pText.includes("Kolding")) {
                                link.click();
                                return true;
                            }
                        }
                        if(parent) parent = parent.parentElement;
                    }
                }
            }
            return false;
        }''')
        
        if not clicked:
            log("Kunne slet ikke finde billet-knappen for Aarhus at klikke på!")
            page.close()
            return state
            
        log("Fandt Aarhus-knappen og klikkede på den! Venter 10 sek. på at pop-up koden hentes...")
        
        # 3. Vent på pop-up / iframe
        page.wait_for_timeout(10000) 
        
        # 4. Hent RÅ KODE fra hovedsiden OG alle indlejrede pop-ups/iframes
        raw_html = page.content()
        for frame in page.frames:
            try:
                raw_html += " " + frame.content()
            except:
                pass
                
        # 5. Brug BeautifulSoup til at rense al HTML-kode væk og kun beholde teksten
        soup = BeautifulSoup(raw_html, "html.parser")
        full_text = soup.get_text(" ").lower()
        full_text = re.sub(r"\s+", " ", full_text).strip()
        
    except Exception as e:
        log(f"Fejl under browser-tjek: {e}")
        page.close()
        return state
        
    page.close()
    
    # 6. Analysér teksten
    no_tickets_phrase = "ingen resalebilletter tilgængeligt pt"
    
    if no_tickets_phrase in full_text:
        currently_available = False
        reason = "Ingen billetter (Udsolgt-tekst fundet)."
    elif "billet ledig" in full_text or "billetter ledig" in full_text or re.search(r"\b[1-9]\d*\s*billet", full_text):
        currently_available = True
        reason = "Positiv tekst fundet (fx 'billet ledig')."
    else:
        currently_available = True
        reason = "Udsolgt-teksten er IKKE fundet i pop-up'en! Mulig billet."
        # Nu vil den printe alt teksten på siden for at vi kan se om den faktisk får fat i pop-up teksten
        log(f"-> Robotten ser nu denne tekst i alt: {full_text[:300]}...")
        
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
    else:
        log(f"Status uændret: {reason}")
        
    state["available"] = currently_available
    return state

def main():
    state = load_state()
    start_time = time.monotonic()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="da-DK"
        )
        
        while True:
            state = check_once(state, context)
            save_state(state)
            if (time.monotonic() - start_time) + CHECK_INTERVAL_SECONDS >= RUN_DURATION_SECONDS:
                break
            time.sleep(CHECK_INTERVAL_SECONDS)
            
        browser.close()

if __name__ == "__main__":
    main()
