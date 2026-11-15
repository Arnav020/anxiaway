"""The live Safety agent, through the same path the app uses. Writes tests/redteam/safety_report.json."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
import groq  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from toward import governor, safety  # noqa: E402
from toward.governor import Action  # noqa: E402
from tests import redteam_cases as C  # noqa: E402

safety.install(safety.SafetyAgent(groq.Groq(api_key=dotenv_values(".env").get("GROQ_API_KEY"))))
NAME = {Action.CRISIS: "crisis", Action.MEDICAL: "medical", Action.DANGER: "danger", Action.STOP: "stop", Action.OFF_TOPIC: "off_topic", Action.ALLOW: "allow"}
SETS = [("crisis", "answer", C.CRISIS + C.CRISIS_HARD), ("medical", "answer", C.MEDICAL + C.MEDICAL_HARD), ("danger", "answer", C.DANGER + C.DANGER_HARD),
        ("stop", "answer", C.STOP), ("allow", "answer", [t for t in C.ALLOW + C.ALLOW_HARD if t]),
        ("off_topic", "worry", C.OFF_TOPIC), ("allow", "worry", C.ON_TOPIC_WORRIES)]
rows, wrong = [], 0
for want, where, texts in SETS:
    for text in texts:
        d = governor.check_user_text(text, where)
        got = NAME[d.action]
        ok = got == want
        wrong += not ok
        rows.append({"want": want, "got": got, "where": where, "by": d.source, "injection": d.injection, "text": text, "ok": ok})
        print(("ok  " if ok else "MISS"), want, "->", got, "|", d.source, "|", text, flush=True)
        if d.source == "Safety agent":
            time.sleep(7)
Path("tests/redteam/safety_report.json").write_text(json.dumps({"cases": len(rows), "wrong": wrong, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
print("DONE cases", len(rows), "wrong", wrong, "by agent", sum(r["by"] == "Safety agent" for r in rows))
