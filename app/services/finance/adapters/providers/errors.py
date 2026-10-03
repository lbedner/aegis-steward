"""The one error every aggregator client raises.

Callers that sync across providers catch ``ProviderError``, so a new
provider needs no new ``except`` clause anywhere.
"""


class ProviderError(RuntimeError):
    """An aggregator API error (``error_code`` carries the machine-readable
    reason - the HTTP status when nothing better is available)."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        # The reason alone, for a person to read; ``str()`` adds the code.
        self.message = message
        super().__init__(f"{error_code}: {message}")
