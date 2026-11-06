# Toward

An agentic AI prototype for **Healthcare & Human Well-being**: practise facing anxiety around high stakes speaking, with a live AI panel that adapts to your body, under safety rules no language model can override.

Built for Agentic AI Hackathon 2026, on the foundations of our final year capstone at Thapar Institute on multimodal anxiety detection.

**This is a research prototype. It isn't therapy, diagnosis or a medical device, and nothing has shown yet that it helps.**

## What's in it
| Part | Runs on | Decides |
|---|---|---|
| Signal Layer | Fixed code | Anxiety-like state from ECG, PPG, skin conductance and motion, relative to a personal baseline |
| Safety Governor | Fixed code | Allow, pause, stop or refer |
| Difficulty policy | Fixed code | Level changes. High arousal alone never lowers difficulty |
| Ladder Planner, Director, Scene Agent, Reporter | Groq, with scripted fallback | Plan, tactic and wording, inside those limits |
| Voice | Whisper, and Orpheus or browser speech | Hearing and measuring answers, speaking lines |

Language models never receive vitals or questionnaire scores. Only the Director sees a state label, and fixed code limits which tactics it may choose.

## Built vs planned
| Working now | Not built yet |
|---|---|
| Multi agent loop and live panel | Live connection from the wearable to this demo |
| Voice answers, with measured pauses | A validated anxiety detection model |
| Signal logic on a **synthetic** replay | Multi day memory and next day re rating |
| Safety Governor, judge console | Counsellor review of rules and thresholds |

All thresholds are starting design values, not clinically validated. The panelist portraits are AI generated characters, not real people.

## Run it locally
Add `GROQ_API_KEY` to `.env`, then from this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest httpx
.\.venv\Scripts\python.exe server.py
```

Open http://127.0.0.1:8765. Run the tests with `.\.venv\Scripts\python.exe -m pytest tests -q`.

## Deployment
The server is stateless: sessions travel in a signed token, so it runs on serverless hosts. Set `GROQ_API_KEY` and a long random `TOWARD_SECRET` in the host's environment. Never commit `.env`.

To replay a real recording, set `TOWARD_REPLAY_CSV` to a file with the columns `t_s, ecg_hr, ppg_hr, eda_us, motion_g, quality`, and the on-screen label changes to match.

## Assets
Geist font (OFL). Phosphor icons (MIT). Panelist portraits generated with ChatGPT.
