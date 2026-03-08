"""Serial transport ownership for the simulator."""


class SerialLinkManager:
    """Own exactly one serial endpoint for the simulator runtime."""

    def __init__(self, port: str | None = None) -> None:
        self.port = port
