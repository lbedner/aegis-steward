"""One ``secrets`` group: the component's commands and steward's key
rotation side by side, neither hiding the other."""

from tests._cli import invoke


def test_rekey_sits_beside_the_components_commands() -> None:
    result = invoke(["secrets", "--help"])
    assert result.exit_code == 0
    for command in ("list", "set", "delete", "test", "rekey"):
        assert command in result.output
