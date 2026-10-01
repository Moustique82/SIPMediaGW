#!/usr/bin/env python

import sys
import os
import time
import traceback
from browsing import Browsing
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

# Texts of the button that enters the meeting from the prejoin screen
JOIN_TEXTS = ("Participer à la réunion", "Participer", "Participer maintenant", "Demander à participer",
              "Join now", "Join meeting", "Ask to join")


class Googlemeet(Browsing):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Hide Chromium headless/automation fingerprint from Google Meet
        if self.chromeOptions:
            self.chromeOptions.add_argument(
                '--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'
            )
            self.chromeOptions.add_argument('--disable-blink-features=AutomationControlled')

    def loadPage(self):
        # Navigate to Google Meet room — URL format: meet.google.com/xxx-yyyy-zzz
        self.driver.get("https://{}/{}".format(
            self.room['config']['webrtc_domain'],
            self.room['roomName']
        ))

        # Hide navigator.webdriver property (checked by Google)
        try:
            self.driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
        except Exception:
            pass

        # Dismiss cookie consent popup if present (incognito / first launch)
        try:
            consent_btn = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "div.TZFSLb button"))
            )
            consent_btn.click()
            print("Google Meet: consent popup dismissed", flush=True)
        except Exception:
            pass

        # Wait for the prejoin screen — use aria-label (stable, #cXX ID is dynamic)
        try:
            WebDriverWait(self.driver, 60).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "input[aria-label='Votre nom'], input[aria-label='Your name']"
                ))
            )
        except Exception:
            print("Google Meet: name input not found, continuing anyway", flush=True)

    def join(self):
        # The prejoin screen is handled here, with real input events: Meet does not
        # act on the synthetic events a page script sends (1 Oct 2026: name shown,
        # "Participer" never taken). The connector script then only finds the call.
        try:
            self.prejoin()
        except Exception as e:
            print("Google Meet: prejoin failed: {}".format(e), flush=True)
        super().join()

    def joinButtons(self):
        # The join button holds several spans: match the label on the button or
        # on one of its spans, exactly ("Autres façons de participer" must not match)
        paths = []
        for text in JOIN_TEXTS:
            paths.append('//button[normalize-space(.)="{}"]'.format(text))
            paths.append('//button[.//*[normalize-space(text())="{}"]]'.format(text))
        return [b for b in self.driver.find_elements(By.XPATH, " | ".join(paths))
                if b.is_displayed() and b.is_enabled() and b.get_attribute("aria-disabled") != "true"]

    def inCall(self):
        return bool(self.driver.find_elements(By.CSS_SELECTOR, "button[jsname='CQylAd']"))

    def prejoin(self):
        name = self.room['displayName']
        field = WebDriverWait(self.driver, 60).until(EC.element_to_be_clickable((By.CSS_SELECTOR,
            "input[aria-label='Votre nom'], input[aria-label='Your name']")))
        # Typed key by key, so that Meet registers it before the button is pressed
        field.click()
        field.send_keys(Keys.CONTROL, "a")
        field.send_keys(Keys.DELETE)
        field.send_keys(name)
        WebDriverWait(self.driver, 10).until(lambda d: field.get_attribute("value") == name)
        print("Google Meet: name typed", flush=True)
        deadline = time.time() + 60
        while time.time() < deadline and not self.inCall():
            buttons = self.joinButtons()
            if not buttons:
                time.sleep(1)
                continue
            time.sleep(1)               # let Meet take the name before the press
            ActionChains(self.driver).move_to_element(buttons[0]).click().perform()
            print("Google Meet: join button pressed ({})".format(buttons[0].text.strip()), flush=True)
            end = time.time() + 10
            while time.time() < end and not self.inCall():
                time.sleep(1)
        print("Google Meet: {}".format("in the call" if self.inCall() else "still on the prejoin screen after 60 s"),
              flush=True)

    def chatHandler(self):
        pass

    def unset(self):
        try:
            self.driver.execute_script(
                "if ( window.meeting ) { window.meeting.leave(); }"
            )
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            print("Meeting logout error: {}".format(e), flush=True)
