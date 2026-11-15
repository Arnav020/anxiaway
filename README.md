# Toward

An agentic AI care prototype for **Healthcare & Human Well-being**, focused on anxiety. It checks anxiety with GAD-7 and a wearable, keeps what the person reports apart from what their body shows, and runs an agent that decides the next step, follows up, and replans from what really happened.

Built for Agentic AI Hackathon 2026, on the foundations of our final year capstone at Thapar Institute on multimodal anxiety detection.

**This is a research prototype. It isn't therapy, diagnosis or a medical device, and nothing has shown yet that it helps.**

## The app
| Screen | Path | What it does |
|---|---|---|
| Landing | `/` | What Toward is, for a first visit |
| Home | `/home` | Latest check, body reading, predictions, and the agent's next step |
| Check | `/checkin` | Consent, safety questions, GAD-7, daily life impact, optional body reading, then one result |
| Vitals | `/vitals` | The wearable, live signals, the sensor mask, and how a reading is used |
| Practice | `/practice`, `/session` | Guided voice session, breathing pause, the week's real world step |
| Record | `/record` | Checks over time, and predictions against outcomes |
| How it's built | `/about` | Theme fit, architecture, prior work, honest status, live pitch mode |

Checks, predictions and preferences are stored only in the browser. The server is stateless.

## The check: three sources, never one score
1. **What you reported:** GAD-7 and the daily life question, scored by fixed code.
2. **What your body showed:** the Signal Layer compares a 45 second reading with the person's own baseline. Movement and weak signals are rejected.
3. **What the model says:** our anxiety model's verdict. Simulated and labelled until the model is validated and connected.

Fixed code states whether the answers and the reading agree, and never merges them. A check that passes the safety rules returns a signed token, valid for 24 hours, which a guided session needs to start.

## What decides what
| Part | Runs on | Decides |
|---|---|---|
| Safety Governor | Fixed code | Allow, pause, stop or refer |
| Check and trend | Fixed code | Score, band, agreement, rising trend, when a person or a new check must come first |
| Signal Layer | Fixed code | State from ECG, PPG, skin conductance and motion, against a personal baseline |
| Anxiety model verdict | Simulated today, our model next | Anxiety-like label and probability, with missing inputs masked |
| Care agent | Groq with tools, scripted fallback | What to read, when to follow up, and the next step, chosen among the steps fixed code allows |
| Planner, Director, Guide, Summary | Groq, with scripted fallback | Plan, tactic and wording, inside those limits |

Language models get the GAD-7 band, strongest concerns, check scores and verdict labels. They never receive raw vitals, individual GAD-7 answers or safety answers.

## Connecting the wearable
The device link is built and tested, and stays off until the device API address is set:

```
TOWARD_DEVICE_API_URL=https://<the API our mobile app uses>/latest
TOWARD_DEVICE_API_KEY=<optional bearer token>
```

When set, `/api/device/status` reports it, the wearable option switches on in the Check and Vitals screens, and `POST /api/reading {"mode": "device"}` pulls the latest readings. Preferred reply, one sample a second:

```json
{
  "samples": [{ "t": 0, "ecg_hr": 78.2, "ppg_hr": 77.9, "eda": 4.01, "motion": 0.03, "quality": 0.95 }],
  "verdict": { "label": "low | moderate | high", "probability": 0.72 },
  "present": ["ECG", "PPG", "EDA", "IMU"]
}
```

Other common field names are accepted, see `toward/device.py`. If the reply differs more than that, `normalise()` is the one function to change. EEG is in the model design and not on the device, so it's always marked absent. A high verdict can only make the agent more careful.

## Voices
Best available first: Orpheus on Groq (needs its terms accepted once on the Groq account), then Kokoro in the browser, then the browser's own voice. Text always appears immediately. Speech recognition uses Whisper on Groq.

## Built vs planned
| Working now | Not built yet |
|---|---|
| Anxiety check with safety routing, trend and result | Live readings from the wearable |
| Care agent with tool calls, follow ups and replanning | A validated anxiety detection model |
| Guided voice sessions with three guide agents | Syncing across devices |
| Signal logic on a **synthetic** stream, device link ready | Counsellor review of rules and thresholds |

All thresholds are starting design values, not clinically validated. The guides are AI generated characters, not clinicians.

## Run it locally
Add `GROQ_API_KEY` to `.env`, then from this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
.\.venv\Scripts\python.exe server.py
```

Open http://127.0.0.1:8765. Run the tests with `.\.venv\Scripts\python.exe -m pytest tests -q`.

## Deployment
The server is stateless: sessions and checks travel in signed tokens. Set `GROQ_API_KEY` and a long random `TOWARD_SECRET` in the host's environment, or tokens stop working whenever the server restarts. Never commit `.env`.

To replay a real recording in guided sessions, set `TOWARD_REPLAY_CSV` to a file with the columns `t_s, ecg_hr, ppg_hr, eda_us, motion_g, quality`.

## Assets
Geist font (OFL). Phosphor icons (MIT), plus a few drawn for this project. Kokoro voice model (Apache 2.0). Guide portraits generated with ChatGPT.
