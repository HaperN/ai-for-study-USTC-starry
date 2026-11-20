"""Historical hard style/report approval is retired; current policy is tested separately."""
from support.retired_cli import assert_retired_cli

def test_historical_cli_rejects_without_rewriting_or_certifying(tmp_path):
    assert_retired_cli(tmp_path, 'style_lint')
