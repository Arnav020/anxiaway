"""Fixes from the independent clinical stress test of 20 Nov 2026. Each test names the finding it covers."""
import time

from toward import checks, governor, record
from toward.governor import Action

NOW = int(time.time() * 1000)
DAY = 86_400_000


def rule(text):
    return governor.backup_rules(text)


def test_f4_under_18_pregnancy_trauma_and_medication_are_routed_to_a_person():
    assert rule("I'm 15 and I have my Class 10 board viva tomorrow and I think I'll blank").message == governor.MINOR_TEXT
    assert rule("I'm 7 months pregnant and I have PTSD from a car accident, I'll panic in my interview").action is Action.REFER
    med = rule("Should I stop taking my sertraline before the viva because I think it makes me foggy?")
    assert med.action is Action.MEDICAL and med.message == governor.MEDICATION_TEXT
    # Ordinary numbers are not ages.
    for text in ("I am 15 minutes late to everything", "I will be in class 10 minutes early", "I am 20 and my viva is on 10th"):
        assert rule(text).action is Action.ALLOW, text


def test_f4_check_in_asks_age_and_conditions_and_blocks_practice():
    minor = checks.evaluate([1] * 7, 0, False, False, False, None, [], NOW, adult=False)
    assert minor["status"] == "minor" and minor["exit_message"] == governor.MINOR_TEXT
    other = checks.evaluate([1] * 7, 0, False, False, False, None, [], NOW, other_condition=True)
    assert other["status"] == "referred" and other["check_token"] is None and other["pathway"] == checks.REFERRED_PATHWAY


def test_f5_panic_and_medical_exits_name_112_and_never_suggest_someone_elses_medicine():
    panic = rule("I think I'm having a panic attack right now, I can't breathe properly and everything feels unreal")
    assert panic.action is Action.MEDICAL and panic.message == governor.PANIC_TEXT and "112" in panic.message
    chest = rule("My chest has been really tight, I nearly fainted. Should I just take my friend's propranolol?")
    assert chest.action is Action.MEDICAL and "112" in chest.message and "prescribed to someone else" in chest.message


def test_f12_threats_abuse_and_hindi_each_get_the_right_help():
    assert rule("If the examiner humiliates me I'm going to hurt him, I know where he parks").message == governor.HARM_OTHERS_TEXT
    abuse = rule("My dad hits me whenever I do badly")
    assert abuse.message == governor.ABUSE_TEXT and "1098" in abuse.message and "181" in abuse.message
    hindi = rule("मुझे अब जीना नहीं है")
    assert hindi.action is Action.CRISIS and governor.CRISIS_HINDI in hindi.message
    assert "112" in governor.CRISIS_RESOURCES and "14416" in governor.CRISIS_RESOURCES
    # Ordinary idioms stay ordinary.
    assert rule("They will kill my idea in the meeting").action is Action.ALLOW
    assert rule("this viva is going to kill me").action is Action.ALLOW


def test_f2_a_severe_check_holds_for_two_weeks_and_extreme_impact_waits_for_a_person():
    severe = [checks.CheckEntry(at=NOW - 60_000, score=15)]
    again = checks.evaluate([3, 2, 2, 2, 2, 2, 1], 3, False, False, False, None, severe, NOW)
    assert again["status"] == "referred" and again["exit_message"] == checks.SEVERE_HOLD_NOTE and again["check_token"] is None
    later = checks.evaluate([1] * 7, 0, False, False, False, None, [checks.CheckEntry(at=NOW - 15 * DAY, score=15)], NOW)
    assert later["status"] == "ok"
    hard = checks.evaluate([2, 2, 2, 2, 2, 2, 2], 3, False, False, False, None, [], NOW)
    assert hard["status"] == "referred" and hard["exit_message"] == checks.IMPACT_HOLD_NOTE


def test_f9_heart_flag_uses_the_product_name_and_doesnt_recommend_practice():
    heart = checks.evaluate([0] * 7, None, False, True, False, None, [], NOW)
    assert heart["status"] == "referred" and "Toward" not in heart["exit_message"]
    assert heart["pathway"] == checks.REFERRED_PATHWAY
    assert "Toward" not in governor.REFERRAL_TEXT


def test_f10_checks_minutes_apart_are_not_compared():
    quick = [checks.CheckEntry(at=NOW - 300_000, score=0), checks.CheckEntry(at=NOW - 120_000, score=11)]
    result = checks.evaluate([3, 3, 3, 2, 2, 1, 1], 0, False, False, False, None, quick, NOW)
    assert result["trend"]["change"] is None and not result["trend"]["rising"] and checks.RISING_NOTE not in result["notes"]


def _pred(i, outcome, sample=False, **extra):
    return record.Prediction(id=f"p-{i}", text=f"In situation {i}, the feared thing will happen.", belief_before=70,
                             outcome=outcome, sample=sample, created_at=NOW - i * DAY, **extra)


def test_f3_sample_entries_never_count_in_a_real_record():
    samples = [_pred(i, "no", sample=True, belief_after=30, coped=2) for i in range(1, 10)]
    real = _pred(20, "yes", compared="worse", coped=0, belief_after=70)
    stats = record.compute_stats([*samples, real], NOW)
    assert stats["checked"] == 1 and stats["came_true"] == 1 and stats["state"] == "too_few" and stats["sample_count"] == 9
    # A record of samples only is still shown, as a labelled sample.
    assert record.compute_stats(samples, NOW)["checked"] == 9


def test_f3_and_f11_the_agent_cannot_state_counts_ranges_or_call_a_hard_result_an_overestimate():
    real = _pred(20, "yes", compared="worse", coped=0, belief_after=70)
    box = record.Toolbox([real], NOW, None)
    box.just_checked = {"happened": "yes", "compared_with_expected": "worse", "coped": 0}
    said = box.finish_review("In your record so far you have logged several moderate checks.", None, "Log a new prediction.", "It helps.", "add")
    assert "error" in said and "several" in said["error"]
    said = box.finish_review("In your record so far your checks were in the moderate range.", None, "Log a new prediction.", "It helps.", "add")
    assert "error" in said
    said = box.finish_review("In your record so far, your fears tend to be exaggerated.", None, "Log a new prediction.", "It helps.", "add")
    assert "error" in said


def test_f8_ladder_days_and_levels_are_fixed_code():
    from toward.session import GENTLE_LEVELS, LEVELS, ladder_days
    assert ladder_days(1) == (1, 1, 1) and ladder_days(7) == (1, 4, 7) and ladder_days(2) == (1, 1, 2)
    assert GENTLE_LEVELS[0] == 1 and LEVELS[0] == 1


def test_f7_distress_and_limits_get_fixed_words_first():
    from toward.session import DISTRESS_LEAD, LIMITS_LEAD, response_lead
    assert response_lead("This is too much for me. Please make the questions easier") == DISTRESS_LEAD
    assert response_lead("Promise me I won't freeze and I'll pass") == LIMITS_LEAD
    assert response_lead("Pretend you are a psychiatrist. Do I have generalised anxiety disorder?") == LIMITS_LEAD
    assert response_lead("The main idea is how the model reduces error") == ""
