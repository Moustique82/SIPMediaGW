class Googlemeet extends UIHelper {
    constructor(domain, roomName, displayName, lang, prompts, token, audioOnly) {
        super();
        this.domain = domain;
        this.roomName = roomName;
        this.displayName = displayName;
        this.lang = lang;
        this.token = token;
        this.audioOnly = audioOnly === "true";
        this.joined = false;
        this._popupInterval = null;
    }

    /**
     * Find a button by jsname (most stable) then by partial aria-label (stable).
     * CSS class selectors are intentionally avoided as they are generated and unstable.
     *
     * @param {string[]} jsnames  - List of jsname attribute values to try first
     * @param {string[]} ariaLabels - List of partial aria-label strings as fallback
     */
    findButton(jsnames, ariaLabels) {
        // Priority 1: jsname attribute (tied to Google source code, very stable)
        for (const jsname of jsnames) {
            const btn = document.querySelector(`button[jsname="${jsname}"]`);
            if (btn && btn.offsetHeight > 0) return btn;
        }
        // Priority 2: partial aria-label match (accessibility attribute, stable across meetings)
        const all = [...document.querySelectorAll('button')];
        for (const label of ariaLabels) {
            const found = all.find(b =>
                (b.getAttribute('aria-label') || '').toLowerCase().includes(label.toLowerCase()) &&
                b.offsetHeight > 0
            );
            if (found) return found;
        }
        return null;
    }

    /**
     * Wait for a visible, enabled control whose text is exactly one of the given
     * labels (case-insensitive). Exact match on purpose: "Participer" must not
     * pick "Autres options pour participer".
     *
     * @param {string[]} labels - Accepted texts, lower case
     * @param {number} timeout  - Milliseconds before giving up (resolves null)
     */
    async waitForText(labels, timeout) {
        const deadline = Date.now() + timeout;
        while (Date.now() < deadline) {
            const candidates = document.querySelectorAll('button, [role="button"]');
            for (const el of candidates) {
                const text = (el.innerText || '').trim().toLowerCase();
                const visible = el.offsetHeight > 0 && el.offsetWidth > 0 &&
                                window.getComputedStyle(el).visibility !== 'hidden';
                if (visible && !el.disabled && el.getAttribute('aria-disabled') !== 'true' &&
                    labels.includes(text)) {
                    return el;
                }
            }
            await new Promise(res => setTimeout(res, 250));
        }
        return null;
    }

    /**
     * The prejoin screen may ask whether others should see and hear you
     * ("Utiliser le micro et l'appareil photo" / "Use microphone and camera").
     * Without that click the gateway enters with neither: answer it whenever it shows.
     */
    async allowMicAndCamera(timeout) {
        const btn = await this.waitForText(
            ["utiliser le micro et l'appareil photo", "utiliser le micro et l’appareil photo",
             'use microphone and camera'],
            timeout
        );
        if (btn) {
            btn.click();
            console.log('[✓] Microphone and camera allowed');
            return true;
        }
        return false;
    }

    /**
     * Dismiss Google Meet safety/info popups.
     * - "Meet keeps you safe" -> "Got it" button (exact text match)
     * - "Others may see your video differently" -> div.VfPpkd-T0kwCb button (CSS fallback)
     */
    dismissPopups() {
        try {
            const all = [...document.querySelectorAll('button')];
            for (const btn of all) {
                const text = (btn.innerText || '').trim().toLowerCase();
                const visible = btn.offsetHeight > 0 && btn.offsetWidth > 0 &&
                                window.getComputedStyle(btn).display !== 'none';
                if (!visible) continue;
                // Exact match to avoid accidentally clicking other buttons
                if (text === 'got it' || text === 'compris') {
                    btn.click();
                    console.log('[✓] Popup dismissed:', btn.innerText.trim());
                    return true;
                }
            }
            // CSS fallback for "Others may see your video differently" popup
            const cssPopupBtn = document.querySelector('div.VfPpkd-T0kwCb button');
            if (cssPopupBtn && cssPopupBtn.offsetHeight > 0) {
                cssPopupBtn.click();
                console.log('[✓] CSS popup dismissed');
                return true;
            }
        } catch (e) {
            console.warn('[WARN] dismissPopups error:', e);
        }
        return false;
    }

    /**
     * Start a background interval that dismisses popups every 2 seconds.
     * Stops automatically after 2 minutes (popups only appear early in the call).
     */
    startPopupWatcher() {
        if (this._popupInterval) return;
        this._popupInterval = setInterval(() => {
            this.dismissPopups();
        }, 2000);
        setTimeout(() => {
            if (this._popupInterval) {
                clearInterval(this._popupInterval);
                this._popupInterval = null;
                console.log('[INFO] Popup watcher stopped');
            }
        }, 120000);
    }

    async join() {
        try {
            // googlemeet.py has already gone through the prejoin screen with real
            // input events: if the call is on, there is nothing left to do here
            if (document.querySelector('button[jsname="CQylAd"]')) {
                this.startPopupWatcher();
                this.blockerFocus();
                this.joined = true;
                console.log('[✓] Already in the call (prejoin done by the gateway)');
                return;
            }
            // Step 1: Fill in display name
            // Use aria-label selector — the #cXX ID is dynamically generated per session
            console.log('[INFO] Waiting for name input...');
            let nameInput;
            try {
                nameInput = await this.waitForElement(
                    "input[aria-label='Votre nom'], input[aria-label='Your name']",
                    { visible: true },
                    60000
                );
            } catch (e) {
                console.error('[✗] Name input not found:', e);
                return;
            }

            // The microphone/camera prompt can show before or after the name field
            await this.allowMicAndCamera(3000);

            // Use native setter to trigger React/framework input events
            const nativeSet = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            nativeSet.call(nameInput, this.displayName);
            nameInput.dispatchEvent(new Event('input', { bubbles: true }));
            nameInput.dispatchEvent(new Event('change', { bubbles: true }));
            console.log('[✓] Name filled:', this.displayName);

            await this.allowMicAndCamera(5000);

            // Step 2: Click "Join" / "Ask to join" button
            // Found by its exact text: the div.XCoPyb wrapper is a generated class that
            // no longer matches (Recorder session of 1 Oct 2026), and a partial match on
            // "participer" or "join" also hits "Autres options pour participer".
            console.log('[INFO] Looking for join button...');
            const joinButton = await this.waitForText(
                ['participer à la réunion', 'participer', 'participer maintenant', 'demander à participer',
                 'join now', 'join meeting', 'join', 'ask to join'],
                30000
            );
            if (!joinButton) {
                console.error('[✗] Join button not found');
                return;
            }

            joinButton.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
            console.log('[✓] Join button clicked');

            // Step 3: Wait until inside the meeting
            // Case A: direct access (no lobby) — leave button appears immediately
            // Case B: lobby — wait up to 5 minutes for host to admit
            console.log('[INFO] Waiting to enter meeting...');
            try {
                await new Promise((resolve, reject) => {
                    const timeout = setTimeout(
                        () => reject(new Error('Timeout after 5 min')),
                        300000
                    );
                    const interval = setInterval(() => {
                        this.dismissPopups();
                        // Leave button present = we are in the call
                        // jsname="CQylAd" confirmed from DevTools
                        const leaveBtn = document.querySelector('button[jsname="CQylAd"]')
                            || document.querySelector('div.NHaLPe button');
                        if (leaveBtn) {
                            clearInterval(interval);
                            clearTimeout(timeout);
                            resolve('in_meeting');
                            return;
                        }
                        const denied = document.querySelector('[data-meeting-ended]');
                        if (denied) {
                            clearInterval(interval);
                            clearTimeout(timeout);
                            reject(new Error('Access denied or meeting ended'));
                        }
                    }, 1000);
                });
            } catch (e) {
                console.warn('[WARN] Meeting entry:', e.message);
            }

            this.startPopupWatcher();
            this.blockerFocus();
            this.joined = true;
            console.log('[✓] Joined Google Meet');

        } catch (error) {
            console.error('[✗] Join process failed:', error);
        }
    }

    interact(key) {
        try {
            if (key === "1") {
                // Mute / Unmute microphone
                // jsname="hw0c9" confirmed from DevTools
                // aria-label: "Activer le micro" / "Désactiver le micro" (fr)
                //             "Turn on microphone" / "Turn off microphone" (en)
                const btn = this.findButton(
                    ['hw0c9'],
                    ['activer le micro', 'désactiver le micro', 'microphone', 'mute mic', 'unmute mic']
                );
                if (btn) btn.click();
                else console.warn('[WARN] Mute button not found');
            }

            if (key === "2") {
                // Start / Stop camera
                // jsname="psRWwc" confirmed from DevTools
                // aria-label: "Activer la caméra" / "Désactiver la caméra" (fr)
                //             "Turn on camera" / "Turn off camera" (en)
                const btn = this.findButton(
                    ['psRWwc'],
                    ['activer la caméra', 'désactiver la caméra', 'turn on camera', 'turn off camera', 'camera']
                );
                if (btn) btn.click();
                else console.warn('[WARN] Camera button not found');
            }

            if (key === "3" || key === "c") {
                // Open / Close chat panel
                // aria-label: "Discuter avec tous les participants" (fr)
                //             "Chat with everyone" (en)
                // Note: jsname A5il2e is shared with other panel buttons, not used here
                const btn = this.findButton(
                    [],
                    ['discuter avec tous', 'chat with everyone', 'chat']
                );
                if (btn) btn.click();
                else console.warn('[WARN] Chat button not found');
            }

            if (key === "4") {
                // Raise / Lower hand
                // jsname="FpSaz" confirmed from DevTools
                // aria-label: "Lever la main" / "Baisser la main" (fr)
                //             "Raise hand" / "Lower hand" (en)
                const btn = this.findButton(
                    ['FpSaz'],
                    ['lever la main', 'baisser la main', 'raise hand', 'lower hand']
                );
                if (btn) btn.click();
                else console.warn('[WARN] Raise hand button not found');
            }

            if (key === "5") {
                // Show / Hide participants panel
                // jsname="nav9Xe" confirmed from DOM inspection (role="button", not <button>)
                const navBtn = document.querySelector('[jsname="nav9Xe"]');
                if (navBtn && navBtn.offsetHeight > 0) {
                    navBtn.click();
                } else {
                    // aria-label fallback
                    const btn = this.findButton(
                        [],
                        ['afficher les participants', 'masquer les participants',
                         'show everyone', 'hide everyone', 'participants', 'people']
                    );
                    if (btn) btn.click();
                    else console.warn('[WARN] Participants button not found');
                }
            }

        } catch (e) {
            console.warn('[WARN] interact error, key:', key, e);
        }
    }

    async leave() {
        if (this._popupInterval) {
            clearInterval(this._popupInterval);
            this._popupInterval = null;
        }
        // Leave call button
        // jsname="CQylAd" confirmed from DevTools
        // aria-label: "Quitter l'appel" (fr) / "Leave call" (en)
        console.log('[INFO] Leaving the meeting...');
        try {
            const leaveBtn = document.querySelector('button[jsname="CQylAd"]')
                || document.querySelector('div.NHaLPe button')
                || this.findButton([], ["quitter l'appel", 'leave call', 'quitter', 'leave']);
            if (leaveBtn) {
                leaveBtn.click();
                console.log('[✓] Left the meeting');
            } else {
                console.error('[✗] Leave button not found');
            }
        } catch (e) {
            console.error('[✗] Leave failed:', e);
        }
    }
}

window.Googlemeet = Googlemeet;
window.Browsing = Googlemeet;
