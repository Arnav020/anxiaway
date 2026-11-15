import sys
sys.path.insert(0, ".")
from toward import governor, record
from toward.governor import Action
from tests.redteam_cases import CRISIS, MEDICAL, DANGER, STOP, ALLOW

def outcome(t):
    if not t: return "allow"
    d = governor.check_user_text(t)
    if d.action is Action.CRISIS: return "crisis"
    if d.action is Action.MEDICAL: return "medical"
    if record._REAL_DANGER.search(t): return "danger"
    if d.action is Action.STOP: return "stop"
    return "allow"

fails = [(w, outcome(t), t) for w, g in (("crisis", CRISIS), ("medical", MEDICAL), ("danger", DANGER), ("stop", STOP), ("allow", ALLOW)) for t in g if outcome(t) != w]
print("cases", sum(map(len, (CRISIS, MEDICAL, DANGER, STOP, ALLOW))), "failures", len(fails))
for f in fails: print(f)
