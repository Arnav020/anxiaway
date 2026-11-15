"""Which model and reasoning level reads quiet crisis signs best, without flagging ordinary worries."""
import json, sys, time
sys.path.insert(0, ".")
import groq
from dotenv import dotenv_values
from toward import safety
from tests.redteam.safety_holdout_cases import CASES
c = groq.Groq(api_key=dotenv_values(".env").get("GROQ_API_KEY")).with_options(timeout=20, max_retries=2)
for model, effort in [("openai/gpt-oss-safeguard-20b", "medium"), ("openai/gpt-oss-120b", "low"), ("openai/gpt-oss-120b", "medium")]:
    wrong, toks, t0 = [], 0, time.time()
    for want, where, text in CASES:
        try:
            r = c.chat.completions.create(model=model, max_completion_tokens=1200, response_format={"type": "json_object"}, reasoning_effort=effort,
                messages=[{"role": "system", "content": safety.POLICY}, {"role": "user", "content": json.dumps({"where": where, "message": text})}])
            got = json.loads(r.choices[0].message.content)["label"]; toks += r.usage.total_tokens
        except Exception as e:
            got = "ERR " + type(e).__name__
        if got == "off_topic" and where != "worry": got = "ok"
        if got != want: wrong.append((want, got, text))
        time.sleep(9 if "safeguard" in model else 3)
    print(f"{model} {effort}: {len(CASES)-len(wrong)}/{len(CASES)} right, avg {toks//len(CASES)} tokens, {time.time()-t0:.0f}s", flush=True)
    for w in wrong: print("   MISS", w, flush=True)
