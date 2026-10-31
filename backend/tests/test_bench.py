from app.analysis.types import AnalysisConfig
from app.bench.generator import random_scenario
from app.bench.runner import aggregate, evaluate_scenario


def test_generator_is_deterministic():
    a, b = random_scenario(5, 1, minutes=20), random_scenario(5, 1, minutes=20)
    assert a.records == b.records
    assert a.ground_truth() == b.ground_truth()
    assert random_scenario(6, 1, minutes=20).records != a.records


def test_ground_truth_includes_fault_service_and_propagation():
    sc = random_scenario(5, 2)
    for f in sc.faults:
        assert f.service in f.affected_services
        assert f.start < f.end


def test_evaluate_scenario_rows_and_aggregate():
    sc = random_scenario(9, 0)
    rows = evaluate_scenario(sc, ["rules_only", "hybrid"], AnalysisConfig())
    assert len(rows) == 2 * len(sc.faults)
    metrics = aggregate(rows)
    assert metrics["scenarios"] == 1
    assert 0 <= metrics["anomaly_precision"] <= 1 and 0 <= metrics["anomaly_recall"] <= 1
    hybrid = metrics["strategies"]["hybrid"]
    assert hybrid["citations_total"] > 0 and hybrid["invalid_citation_rate"] == 0
