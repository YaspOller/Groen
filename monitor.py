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
CHECK_INTERVAL_SECONDS = 30  # Tjekker hvert halve minut

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
        
        # 1. Klik cookie-boks væk, hvis den er i vejen
        try:
            page.locator("button, a", has_text=re.compile(r"accepter|tillad", re.I)).first.click(timeout=2000)
        except:
            pass
            
        # 2. SUPER-SØGEMASKINE TIL AT FINDE KNAPPEN:
        clicked = page.evaluate('''() => {
            let links = Array.from(document.querySelectorAll("a, button"));
            for (let link of links) {
                let txt = (link.innerText || "").toLowerCase();
                if (txt.includes("køb") || txt.includes("resale") || txt.includes("venteliste")) {
                    let parent = link.parentElement;
                    // Gå op til 8 niveauer op for at finde "kassen"
                    for(let i=0; i<8; i++) {
                        if (parent && parent.innerText) {
                            let pText = parent.innerText;
                            // Tjek om vi er i Aarhus' kasse, og IKKE har fået nabo-byerne med
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
        else:
            log("Fandt Aarhus-knappen og klikkede på den! Venter 10 sekunder på pop-up...")
        
        # 3. Vent 10 sekunder for at pop-up/billetsystem loader 100%
        page.wait_for_timeout(10000) 
        
        full_text = ""
        # 4. Saml alt tekst fra skærmen (inklusive det indlejrede billetsystem)
        for p in context.pages:
            try:
                full_text += " " + p.inner_text("body", timeout=2000)
                for frame in p.frames:
                    try:
                        full_text += " " + frame.locator("body").inner_text(timeout=2000)
                    except:
                        pass
            except:
                pass
                
        full_text = re.sub(r"\s+", " ", full_text).strip().lower()
        
    except Exception as e:
        log(f"Fejl under browser-tjek: {e}")
        page.close()
        return state
        
    page.close()
    
    # 5. Analysér teksten
    no_tickets_phrase = "ingen resalebilletter tilgængeligt pt"
    
    if no_tickets_phrase in full_text:
        currently_available = False
        reason = "Ingen billetter (Udsolgt-tekst fundet)."
    elif "billet ledig" in full_text or "billetter ledig" in full_text or re.search(r"\b[1-9]\d*\s*billet", full_text):
        currently_available = True
        reason = "Positiv tekst fundet (fx 'billet ledig')."
    else:
        currently_available = True
        reason = "Udsolgt-teksten er forsvundet! Mulig billet."
        log(f"-> Robotten ser dette på skærmen (uddrag): {full_text[:300]}...")
        
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
