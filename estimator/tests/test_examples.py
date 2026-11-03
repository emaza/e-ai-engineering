from app.context.examples import (
    CANONICAL_EXAMPLES,
    ESTIMATION_EXAMPLES,
    HOURLY_RATES,
    render_estimation,
)
from app.services.llm_service import build_system_prompt


def test_at_least_two_examples() -> None:
    assert len(ESTIMATION_EXAMPLES) >= 2
    for example in ESTIMATION_EXAMPLES:
        assert example["meeting_summary"].strip()
        assert example["estimation"].startswith("## Estimación:")


def test_totals_match_breakdown() -> None:
    for example in CANONICAL_EXAMPLES:
        assert example.total_hours == sum(task.hours for task in example.tasks)
        expected_cost = sum(task.hours * HOURLY_RATES[task.role] for task in example.tasks)
        assert example.total_cost == expected_cost
        assert f"**Total estimado: {example.total_hours} horas**" in render_estimation(example)


def test_inventory_example_matches_statement() -> None:
    # El ejemplo orientativo del enunciado suma 175 horas
    assert CANONICAL_EXAMPLES[0].total_hours == 175


def test_examples_are_injected_in_system_prompt() -> None:
    prompt = build_system_prompt()
    for example in ESTIMATION_EXAMPLES:
        assert example["meeting_summary"] in prompt
        assert example["estimation"] in prompt
