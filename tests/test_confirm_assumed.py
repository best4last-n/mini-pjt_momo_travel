"""가정·미정으로 남은 조건을 같은 값으로 다시 고르면 '확정'이 되어 진행률 100%에 들어가야 한다(2026-11-27 사용자 지적)."""
from src.counsel import agent
from src.planning import brochure


def test_confirm_same_value_clears_assumption(monkeypatch):
    calls = {}

    def fake_reschedule(state, spec, days, changes, step, repair=False):
        calls["spec"], calls["changes"] = spec, changes
        return {"status": "승인 대기", "changes": changes, "conditions": brochure.checklist(spec)}
    monkeypatch.setattr(agent, "_reschedule", fake_reschedule)
    monkeypatch.setattr(agent, "_run_plan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("다시 짜면 안 됨")))
    monkeypatch.setattr(agent, "_day_picks", lambda spec, items: [])
    spec = agent.planner.TripSpec(region="gyeongju", start=agent.today(), nights=1,
                                  assumptions=["long_transport", "local_transport", "intensity"])
    st = {"spec": agent.spec_to_dict(spec), "itinerary": [{"x": 1}], "trace": [],
          "set_req": {"changes": [{"key": "intensity", "value": spec.intensity, "confirm": True},
                                  {"key": "local_transport", "value": spec.local_transport, "confirm": True}]}}
    out = agent.set_node(st)
    left = set(calls["spec"].assumptions)
    assert "intensity" not in left and "local_transport" not in left and "long_transport" in left
    conds = {c["key"]: c["status"] for c in out["conditions"]}
    assert conds["intensity"] == "confirmed" and conds["local_transport"] == "confirmed"
    assert "확정했어요" in calls["changes"][0]
