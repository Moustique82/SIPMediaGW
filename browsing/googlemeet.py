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
from selenium.common.exceptions import StaleElementReferenceException

# Texts Meet shows when it turns the guest away
REFUSED_TEXTS = ("Vous ne pouvez pas participer à cet appel vidéo", "You can't join this video call")

# Texts of the button that enters the meeting from the prejoin screen
JOIN_TEXTS = ("Participer à la réunion", "Participer", "Participer maintenant", "Demander à participer",
              "Join now", "Join meeting", "Ask to join")


class Googlemeet(Browsing):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # No fixed user agent: a "Chrome/146" string that does not match the real
        # Chromium version (navigator.userAgentData keeps the real one) is itself a
        # bot signal. Only the automation flag is turned off here.
        if self.chromeOptions:
            self.chromeOptions.add_argument('--disable-blink-features=AutomationControlled')
            # Test (1 Oct 2026): Meet turns the gateway away after the join button while
            # a normal browser on the same network gets in. These two switches weaken the
            # page's security in ways a page can detect; they are dropped for Meet only.
            # Known side effect: the in-meeting menu stylesheet (file://) no longer loads.
            for arg in ('--disable-web-security', '--disable-site-isolation-trials'):
                while arg in self.chromeOptions.arguments:
                    self.chromeOptions.arguments.remove(arg)
            print("Google Meet: browser switches {}".format(" ".join(self.chromeOptions.arguments)), flush=True)

    def cdp(self, cmd, params):
        # webdriver.Remote has no execute_cdp_cmd: declare chromedriver's endpoint
        ex = self.driver.command_executor
        if "executeCdpCommand" not in getattr(ex, "_commands", {}):
            try:
                ex.add_command("executeCdpCommand", "POST", "/session/$sessionId/goog/cdp/execute")
            except AttributeError:
                ex._commands["executeCdpCommand"] = ("POST", "/session/$sessionId/goog/cdp/execute")
        return self.driver.execute("executeCdpCommand", {"cmd": cmd, "params": params})["value"]

    def disguise(self):
        # Run before Meet's own scripts, on every document (execute_script after
        # get() comes too late: Meet has already read navigator.webdriver)
        try:
            self.cdp("Page.addScriptToEvaluateOnNewDocument", {"source":
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"})
        except Exception as e:
            print("Google Meet: webdriver not hidden: {}".format(e), flush=True)
        try:
            ua = self.driver.execute_script("return navigator.userAgent")
            if "Headless" in ua:        # AUDIO_ONLY runs headless: same browser, without the word
                self.cdp("Network.setUserAgentOverride", {"userAgent": ua.replace("HeadlessChrome", "Chrome")})
        except Exception as e:
            print("Google Meet: user agent not set: {}".format(e), flush=True)

    def fingerprint(self):
        try:
            fp = self.driver.execute_script(
                "return [navigator.userAgent, navigator.webdriver, "
                "navigator.userAgentData ? navigator.userAgentData.brands.map(b => b.brand + ' ' + b.version).join(', ') : '-']")
            print("Google Meet: browser {} | webdriver={} | brands {}".format(*fp), flush=True)
        except Exception:
            pass

    def refused(self):
        try:
            text = self.driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            return False
        return any(t in text for t in REFUSED_TEXTS)

    def loadPage(self):
        self.disguise()
        # Navigate to Google Meet room — URL format: meet.google.com/xxx-yyyy-zzz
        self.driver.get("https://{}/{}".format(
            self.room['config']['webrtc_domain'],
            self.room['roomName']
        ))

        self.fingerprint()
        if self.refused():
            print("Google Meet: refused by Meet at the meeting page", flush=True)

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
        while time.time() < deadline and not self.inCall() and not self.refused():
            try:
                # found again on every round: Meet redraws the button once the name is in
                buttons = self.joinButtons()
                if not buttons:
                    time.sleep(1)
                    continue
                time.sleep(1)               # let Meet take the name before the press
                label = buttons[0].text.strip()
                ActionChains(self.driver).move_to_element(buttons[0]).click().perform()
            except StaleElementReferenceException:
                continue
            print("Google Meet: join button pressed ({})".format(label), flush=True)
            end = time.time() + 10
            while time.time() < end and not self.inCall() and not self.refused():
                time.sleep(1)
        if self.refused():
            print("Google Meet: refused by Meet after the join button", flush=True)
        else:
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
