# OPERATION NIGHTINGALE — §07 Acceptance Ritual Runbook

**Windows 10/11 x64 Verification Checklist for `Hey Jev`**  
*Port of henryklunaris/hey-jev by Bhagwat Panwar. Original by Henryk Lunaris (MIT).*

This runbook contains every spoken phrase and operational ritual defined in §07 of the OPERATION NIGHTINGALE specification. Each item has a manual verification checkbox (`[ ] Pass  [ ] Fail`) and expected system response.

---

## 0. Prerequisites & Environment Setup

Before starting manual verification:
1. Ensure `Hey Jev.exe` or `python siri.py` is running.
2. In `%APPDATA%\HeyJev\.env` or Windows Credential Manager:
   - Provide `TYPESAFE_API_KEY` (for default Jev decision backend).
   - Provide `FISH_AUDIO_API_KEY` (for TTS synthesis).
   - Provide `OPENROUTER_API_KEY` or `OPENAI_API_KEY` (for Haiku non-commands and dictation).
3. Connect a working microphone and default playback device.

---

## 1. App Control: Launching (`open` / `launch` / `start`)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 1 | *"Hey Jev, open Slack"* | Slack launches or focuses if already running | `[ ] Pass  [ ] Fail` | |
| 2 | *"Hey Jev, launch Spotify"* | Spotify starts up and connects to session | `[ ] Pass  [ ] Fail` | |
| 3 | *"Hey Jev, start Discord"* | Discord application window opens | `[ ] Pass  [ ] Fail` | |
| 4 | *"Hey Jev, open Visual Studio Code"* | VS Code opens workspace | `[ ] Pass  [ ] Fail` | |
| 5 | *"Hey Jev, start Notepad"* | Notepad (`notepad.exe`) window opens | `[ ] Pass  [ ] Fail` | |
| 6 | *"Hey Jev, open Terminal"* | Windows Terminal (`wt.exe` or `cmd.exe`) opens | `[ ] Pass  [ ] Fail` | |
| 7 | *"Hey Jev, open Chrome"* | Google Chrome window opens | `[ ] Pass  [ ] Fail` | |
| 8 | *"Hey Jev, open Edge"* | Microsoft Edge window opens | `[ ] Pass  [ ] Fail` | |
| 9 | *"Hey Jev, open Brave"* | Brave Browser launches | `[ ] Pass  [ ] Fail` | |
| 10 | *"Hey Jev, open Firefox"* | Mozilla Firefox opens | `[ ] Pass  [ ] Fail` | |

---

## 2. App Control: Terminating (`quit` / `close` / `kill`)
*Verification includes graceful `WM_CLOSE`, 1.5s grace period, and process-tree termination for tray-minimizing apps.*

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 11 | *"Hey Jev, quit Slack"* | Slack closes; terminates completely after 1.5s | `[ ] Pass  [ ] Fail` | Verify no zombie tray icon |
| 12 | *"Hey Jev, close Spotify"* | Spotify closes cleanly | `[ ] Pass  [ ] Fail` | |
| 13 | *"Hey Jev, kill Discord"* | Discord process tree terminated | `[ ] Pass  [ ] Fail` | |
| 14 | *"Hey Jev, quit VS Code"* | VS Code prompts if unsaved or closes | `[ ] Pass  [ ] Fail` | |
| 15 | *"Hey Jev, close Notepad"* | Notepad window closes | `[ ] Pass  [ ] Fail` | |
| 16 | *"Hey Jev, quit Terminal"* | Terminal window exits | `[ ] Pass  [ ] Fail` | |
| 17 | *"Hey Jev, quit Chrome"* | Chrome closes completely | `[ ] Pass  [ ] Fail` | |
| 18 | *"Hey Jev, close Edge"* | Edge window closes | `[ ] Pass  [ ] Fail` | |

---

## 3. App Control: Hide & Minimise

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 19 | *"Hey Jev, hide Slack"* | Slack window minimizes to taskbar without bare SW_HIDE | `[ ] Pass  [ ] Fail` | |
| 20 | *"Hey Jev, hide Discord"* | Discord window minimizes | `[ ] Pass  [ ] Fail` | |
| 21 | *"Hey Jev, hide Spotify"* | Spotify window minimizes | `[ ] Pass  [ ] Fail` | |
| 22 | *"Hey Jev, hide Chrome"* | Chrome window minimizes | `[ ] Pass  [ ] Fail` | |
| 23 | *"Hey Jev, minimize Slack"* | Slack minimizes to taskbar | `[ ] Pass  [ ] Fail` | |
| 24 | *"Hey Jev, minimize Discord"* | Discord minimizes to taskbar | `[ ] Pass  [ ] Fail` | |
| 25 | *"Hey Jev, minimize Spotify"* | Spotify minimizes to taskbar | `[ ] Pass  [ ] Fail` | |
| 26 | *"Hey Jev, minimize Chrome"* | Chrome minimizes to taskbar | `[ ] Pass  [ ] Fail` | |
| 27 | *"Hey Jev, minimise VS Code"* | VS Code minimizes to taskbar | `[ ] Pass  [ ] Fail` | |

---

## 4. App Control: Switch & Focus

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 28 | *"Hey Jev, switch to Slack"* | Slack restores and becomes foreground active | `[ ] Pass  [ ] Fail` | |
| 29 | *"Hey Jev, focus Discord"* | Discord window brought to front | `[ ] Pass  [ ] Fail` | |
| 30 | *"Hey Jev, bring up Spotify"* | Spotify window brought to front | `[ ] Pass  [ ] Fail` | |
| 31 | *"Hey Jev, switch to Chrome"* | Chrome window brought to front | `[ ] Pass  [ ] Fail` | |
| 32 | *"Hey Jev, focus Terminal"* | Terminal restored and focused | `[ ] Pass  [ ] Fail` | |

---

## 5. Browser Navigation & URL Launching

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 33 | *"Hey Jev, open youtube.com in Edge"* | Edge navigates to https://youtube.com | `[ ] Pass  [ ] Fail` | |
| 34 | *"Hey Jev, go to github.com in Chrome"* | Chrome navigates to https://github.com | `[ ] Pass  [ ] Fail` | |
| 35 | *"Hey Jev, open reddit.com"* | Default browser navigates to reddit.com | `[ ] Pass  [ ] Fail` | |
| 36 | *"Hey Jev, open new tab"* | Foreground browser opens fresh tab (`Ctrl+T`) | `[ ] Pass  [ ] Fail` | |
| 37 | *"Hey Jev, new tab in Chrome"* | Chrome focused and `Ctrl+T` synthesized | `[ ] Pass  [ ] Fail` | |
| 38 | *"Hey Jev, open google.com"* | Default browser opens https://google.com | `[ ] Pass  [ ] Fail` | |
| 39 | *"Hey Jev, open twitter.com in Brave"* | Brave opens https://twitter.com | `[ ] Pass  [ ] Fail` | |
| 40 | *"Hey Jev, go to news.ycombinator.com"*| Default browser opens Hacker News | `[ ] Pass  [ ] Fail` | |

---

## 6. Master Volume Controls (`pycaw` Endpoint Volume)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 41 | *"Hey Jev, volume up"* | Master volume increases by +20% | `[ ] Pass  [ ] Fail` | |
| 42 | *"Hey Jev, turn it up"* | Master volume increases by +20% | `[ ] Pass  [ ] Fail` | |
| 43 | *"Hey Jev, louder"* | Master volume increases by +20% | `[ ] Pass  [ ] Fail` | |
| 44 | *"Hey Jev, volume down"* | Master volume decreases by -20% | `[ ] Pass  [ ] Fail` | |
| 45 | *"Hey Jev, turn it down"* | Master volume decreases by -20% | `[ ] Pass  [ ] Fail` | |
| 46 | *"Hey Jev, softer"* | Master volume decreases by -20% | `[ ] Pass  [ ] Fail` | |
| 47 | *"Hey Jev, mute"* | System audio endpoint muted | `[ ] Pass  [ ] Fail` | |
| 48 | *"Hey Jev, unmute"* | System audio endpoint unmuted | `[ ] Pass  [ ] Fail` | |
| 49 | *"Hey Jev, silence audio"* | System audio endpoint muted | `[ ] Pass  [ ] Fail` | |
| 50 | *"Hey Jev, mute the computer"* | System audio endpoint muted | `[ ] Pass  [ ] Fail` | |
| 51 | *"Hey Jev, set volume to 40%"* | Master volume set to exactly 40% | `[ ] Pass  [ ] Fail` | |
| 52 | *"Hey Jev, set volume to 20%"* | Master volume set to exactly 20% | `[ ] Pass  [ ] Fail` | |
| 53 | *"Hey Jev, turn volume to 80%"*| Master volume set to exactly 80% | `[ ] Pass  [ ] Fail` | |
| 54 | *"Hey Jev, volume 50 percent"* | Master volume set to exactly 50% | `[ ] Pass  [ ] Fail` | |
| 55 | *"Hey Jev, max volume"* | Master volume set to 100% | `[ ] Pass  [ ] Fail` | |
| 56 | *"Hey Jev, quiet volume"* | Master volume set to 15% | `[ ] Pass  [ ] Fail` | |

---

## 7. Spotify Session Volume (`ISimpleAudioVolume`)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 57 | *"Hey Jev, turn up Spotify"* | Spotify session volume increases by +20% | `[ ] Pass  [ ] Fail` | Master volume stays constant |
| 58 | *"Hey Jev, Spotify volume down"* | Spotify session volume decreases by -20% | `[ ] Pass  [ ] Fail` | |
| 59 | *"Hey Jev, mute Spotify"* | Spotify session muted | `[ ] Pass  [ ] Fail` | Master audio unaffected |
| 60 | *"Hey Jev, set Spotify volume to 30%"*| Spotify session set to exactly 30% | `[ ] Pass  [ ] Fail` | |
| 61 | *"Hey Jev, lower Spotify"* | Spotify session volume decreases | `[ ] Pass  [ ] Fail` | |

---

## 8. Spotify & Media Transport (SMTC State-Aware)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 62 | *"Hey Jev, play music"* | Checks SMTC; sends play only if paused | `[ ] Pass  [ ] Fail` | No double-toggle stutter |
| 63 | *"Hey Jev, resume playback"* | Checks SMTC; resumes media | `[ ] Pass  [ ] Fail` | |
| 64 | *"Hey Jev, pause Spotify"* | Checks SMTC; sends pause only if playing | `[ ] Pass  [ ] Fail` | |
| 65 | *"Hey Jev, pause music"* | Checks SMTC; pauses media | `[ ] Pass  [ ] Fail` | |
| 66 | *"Hey Jev, stop music"* | Checks SMTC; pauses media | `[ ] Pass  [ ] Fail` | |
| 67 | *"Hey Jev, next song"* | Synthesizes `VK_MEDIA_NEXT_TRACK` | `[ ] Pass  [ ] Fail` | Next track begins |
| 68 | *"Hey Jev, skip track"* | Synthesizes `VK_MEDIA_NEXT_TRACK` | `[ ] Pass  [ ] Fail` | |
| 69 | *"Hey Jev, previous song"* | Synthesizes `VK_MEDIA_PREV_TRACK` (double skip if mid-track) | `[ ] Pass  [ ] Fail` | Restarts/skips back |
| 70 | *"Hey Jev, go back a track"* | Goes to previous track | `[ ] Pass  [ ] Fail` | |

---

## 9. Display & Themes (Registry + `WM_SETTINGCHANGE`)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 71 | *"Hey Jev, turn on dark mode"* | Registry AppsUseLightTheme=0; apps turn dark instantly | `[ ] Pass  [ ] Fail` | |
| 72 | *"Hey Jev, enable dark mode"* | System & apps enter dark theme | `[ ] Pass  [ ] Fail` | |
| 73 | *"Hey Jev, light mode"* | Registry AppsUseLightTheme=1; apps turn light instantly | `[ ] Pass  [ ] Fail` | |
| 74 | *"Hey Jev, turn off dark mode"* | Light mode restored | `[ ] Pass  [ ] Fail` | |
| 75 | *"Hey Jev, toggle dark mode"* | Current mode inverted immediately | `[ ] Pass  [ ] Fail` | |
| 76 | *"Hey Jev, switch to dark theme"* | Dark theme activated | `[ ] Pass  [ ] Fail` | |

---

## 10. System State (Lock & Sleep)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 77 | *"Hey Jev, lock the screen"* | `LockWorkStation()` triggered; lock screen shows | `[ ] Pass  [ ] Fail` | |
| 78 | *"Hey Jev, lock computer"* | Windows locked | `[ ] Pass  [ ] Fail` | |
| 79 | *"Hey Jev, lock Windows"* | Windows locked | `[ ] Pass  [ ] Fail` | |
| 80 | *"Hey Jev, go to sleep"* | `SetSuspendState(False, False, False)` triggered | `[ ] Pass  [ ] Fail` | PC enters sleep |
| 81 | *"Hey Jev, put the computer to sleep"*| PC enters sleep | `[ ] Pass  [ ] Fail` | |
| 82 | *"Hey Jev, sleep"* | PC enters sleep | `[ ] Pass  [ ] Fail` | |

---

## 11. Timers & Reminders (Monotonic Async Timers + Toast Notifications)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 83 | *"Hey Jev, set a timer for 5 minutes"* | Timer registered; confirms verbally; toasts at 5m | `[ ] Pass  [ ] Fail` | |
| 84 | *"Hey Jev, timer 10 minutes"* | 10 minute timer started | `[ ] Pass  [ ] Fail` | |
| 85 | *"Hey Jev, set a 25 minute pomodoro timer"*| 25 minute timer started | `[ ] Pass  [ ] Fail` | |
| 86 | *"Hey Jev, set a reminder to call mom in 1 hour"*| Reminder registered with label "call mom" | `[ ] Pass  [ ] Fail` | |
| 87 | *"Hey Jev, remind me to check the oven in 15 minutes"*| Reminder registered with label "check the oven" | `[ ] Pass  [ ] Fail` | |
| 88 | *"Hey Jev, how much time is left on my timer"*| Jev speaks remaining time accurately | `[ ] Pass  [ ] Fail` | Monotonic clock parity |
| 89 | *"Hey Jev, timer status"* | Jev speaks active timer count & remaining durations | `[ ] Pass  [ ] Fail` | |
| 90 | *"Hey Jev, cancel timer"* | Active timer cancelled | `[ ] Pass  [ ] Fail` | |
| 91 | *"Hey Jev, cancel all timers"*| All active timers cancelled | `[ ] Pass  [ ] Fail` | |

---

## 12. Compound Commands (Speculative Fan-Out Two-Action Split)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 92 | *"Hey Jev, pause Spotify and open Slack"* | 1. Pauses Spotify; 2. Opens Slack | `[ ] Pass  [ ] Fail` | No LLM splitter used |
| 93 | *"Hey Jev, mute volume and lock computer"* | 1. Mutes master volume; 2. Locks workstation | `[ ] Pass  [ ] Fail` | |
| 94 | *"Hey Jev, open Chrome and play music"* | 1. Launches Chrome; 2. Resumes media playback | `[ ] Pass  [ ] Fail` | |
| 95 | *"Hey Jev, turn on dark mode and set volume to 50%"* | 1. Toggles dark mode; 2. Sets volume to 50% | `[ ] Pass  [ ] Fail` | |
| 96 | *"Hey Jev, quit Discord and minimize Slack"* | 1. Terminates Discord; 2. Minimizes Slack | `[ ] Pass  [ ] Fail` | |
| 97 | *"Hey Jev, pause music and set a timer for 10 minutes"* | 1. Pauses audio; 2. Starts 10m timer | `[ ] Pass  [ ] Fail` | |

---

## 13. Chit-Chat & Personality

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 98 | *"Hey Jev, hello Jev"* | Plays pre-rendered instant response | `[ ] Pass  [ ] Fail` | Zero API call latency |
| 99 | *"Hey Jev, good morning"* | Plays pre-rendered greeting | `[ ] Pass  [ ] Fail` | |
| 100 | *"Hey Jev, thank you"* | Plays pre-rendered reply (`"You're welcome"`) | `[ ] Pass  [ ] Fail` | |

---

## 14. Non-Commands (Claude Haiku via OpenRouter)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 101 | *"Hey Jev, who wrote Hamlet"* | Routes to Claude Haiku; speaks answer via TTS | `[ ] Pass  [ ] Fail` | |
| 102 | *"Hey Jev, what is the capital of France"* | Speaks: *"The capital of France is Paris."* | `[ ] Pass  [ ] Fail` | |
| 103 | *"Hey Jev, why is the sky blue"* | Speaks concise explanation via TTS | `[ ] Pass  [ ] Fail` | |

---

## 15. Unclear & Near-Miss Rejection

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---|---|---|---|
| 104 | *"Hey Jev, banana potato umbrella"* | Confidence < 0.65; asks: *"Pardon?"* / *"Say again?"* | `[ ] Pass  [ ] Fail` | Strike 1 |
| 105 | *"Hey Jev, asdfghjkl"* (second strike) | Two strikes reached; gives up gracefully | `[ ] Pass  [ ] Fail` | Strike 2 |

---

## 16. Push-To-Talk & Wake Gate Rituals

| # | Test Ritual | Action | Expected Result | Result |
|---|---|---|---|---|
| 106 | **Push-To-Talk (Right Alt)** | Press and hold Right-Alt key, say *"volume up"*, release key | Jev acts immediately upon key release; volume increases | `[ ] Pass  [ ] Fail` |
| 107 | **Wake Gate Prefix Filter** | Say *"I think we should open Slack"* (without wake word) | Ignored completely; no API call triggered | `[ ] Pass  [ ] Fail` |
| 108 | **openWakeWord Engine** | Enable openWakeWord in Settings; speak *"Hey Jev"* softly | Wake engine triggers low-power acoustic detection | `[ ] Pass  [ ] Fail` |

---

## 17. Dictation Mode & Floating Waveform Bubble

| # | Test Ritual | Action | Expected Result | Result |
|---|---|---|---|---|
| 109 | **Raise Waveform Bubble** | Say *"Hey Jev, transcribe"* | Frameless translucent bubble appears at bottom-center above taskbar, topmost, click-through, animating audio waveform | `[ ] Pass  [ ] Fail` |
| 110 | **Transcribe & Paste** | Dictate a sentence into active text area, then say *"Hey Jev, stop transcribing"* | Bubble dismisses; transcribed text pastes directly at cursor; dictation logged to `%APPDATA%\HeyJev\Hey Jev dictation.jsonl` | `[ ] Pass  [ ] Fail` |

---

## 18. Window, Settings, & System Tray Lifecycle

| # | Test Ritual | Action | Expected Result | Result |
|---|---|---|---|---|
| 111 | **Tab Navigation** | Click through Home, Dictionary, Apps, Dictation, Privacy, Settings, Keys | All 7 tabs render with native dark/light styling | `[ ] Pass  [ ] Fail` |
| 112 | **Status Dot** | Inspect status indicator in top corner | Reflects state (Idle = green, Listening = amber, Processing = blue) | `[ ] Pass  [ ] Fail` |
| 113 | **Keep on Top** | Toggle "Keep on Top" checkbox in Settings | Window stays pinned above other windows | `[ ] Pass  [ ] Fail` |
| 114 | **Close to Tray** | Click window close button (top right `X`) | Window hides; icon remains in system tray; Jev continues listening | `[ ] Pass  [ ] Fail` |
| 115 | **Tray Context Menu** | Right-click tray icon; click "Open" | Window un-hides and focuses | `[ ] Pass  [ ] Fail` |
| 116 | **Explicit Quit** | Right-click tray icon; click "Quit" (or press `Ctrl+Q`) | Application exits completely; background threads terminate | `[ ] Pass  [ ] Fail` |

---

### Sign-off Ledger
- **Tester Name**: __________________________
- **Date Tested**: __________________________
- **Total Passed**: _____ / 116
- **Defects Discovered**: ____________________
