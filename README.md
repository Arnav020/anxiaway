# Toward

An agentic AI care prototype for **Healthcare & Human Well-being**, focused on anxiety. It combines GAD-7 screening, wearable readings and an anxiety model's verdict, decides which care step fits, guides the person through it, and produces a health summary they can share.

Built for Agentic AI Hackathon 2026, on the foundations of our final year capstone at Thapar Institute on multimodal anxiety detection.

**This is a research prototype. It isn't therapy, diagnosis or a medical device, and nothing has shown yet that it helps.**

## The journey
1. **Check-in:** the situation, what anxiety predicts, GAD-7 with an immediate result, safety questions and consent.
2. **Connect readings:** wearable, app and model (marked "Integrating soon"), or the labelled demo stream.
3. **Care plan:** fixed code picks the pathway from the GAD-7 band. The planner writes a graded ladder.
4. **Guided session:** AI guides role play the situation by voice or text. Readings and the verdict steer each step.
5. **After the session:** peak anxiety, symptoms, whether the prediction came true, and a new belief rating.
6. **Health summary:** screening, readings, answers, the agent's insight, next steps and a real world test, saved as PDF.

## What decides what
| Part | Runs on | Decides |
|---|---|---|
| Safety Governor | Fixed code | Allow, pause, stop or refer |
| Signal Layer | Fixed code | State from ECG, PPG, skin conductance and motion, against a personal baseline |
| Anxiety model verdict | Simulated today, our model next | Anxiety-like label and probability |
| Care pathway and difficulty policy | Fixed code | Pathway from GAD-7. High arousal or a high verdict never raises difficulty |
| Planner, Director, Guide, Summary | Groq, with scripted fallback | Plan, tactic and wording, inside those limits |

Language models get the GAD-7 band, strongest concerns and verdict label. They never receive raw vitals, individual GAD-7 answers or safety answers.

## Connecting the app and model
`POST /api/answer` already accepts a verdict from the app, and a real one replaces the simulated one and is labelled as such:

```json
{ "model_verdict": { "label": "low | moderate | high | unavailable", "probability": 0.72 } }
```

A high verdict can only make the agent more careful. The UI option stays marked "Integrating soon" until the app sends real data.

## Voices
Best available first: Orpheus on Groq (needs its terms accepted once on the Groq account), then Kokoro in the browser (Fenrir and others, used only when the device is fast enough, and never on phones), then the browser's own voice. Text always appears immediately. Speech recognition uses Whisper on Groq.

## Built vs planned
| Working now | Not built yet |
|---|---|
| GAD-7, pathway, post-session questions, health summary | Live connection to the wearable and app |
| Multi agent loop with fallback, judge console | A validated anxiety detection model |
| Voice sessions with speech recognition | Multi day memory and next day re rating |
| Signal logic on a **synthetic** replay | Counsellor review of rules and thresholds |

All thresholds are starting design values, not clinically validated. The guides are AI generated characters, not clinicians.

## Run it locally
Add `GROQ_API_KEY` to `.env`, then from this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest httpx
.\.venv\Scripts\python.exe server.py
```

Open http://127.0.0.1:8765. Run the tests with `.\.venv\Scripts\python.exe -m pytest tests -q`.

## Deployment
The server is stateless: sessions travel in a signed token. Set `GROQ_API_KEY` and a long random `TOWARD_SECRET` in the host's environment. Never commit `.env`.

To replay a real recording, set `TOWARD_REPLAY_CSV` to a file with the columns `t_s, ecg_hr, ppg_hr, eda_us, motion_g, quality`.

## Assets
Geist font (OFL). Phosphor icons (MIT). Kokoro voice model (Apache 2.0). Guide portraits generated with ChatGPT.
