"""WhatsApp channel via Evolution API. `get_gateway()` returns the process-wide gateway."""

from iler.whatsapp.base import WhatsAppGateway

_gateway: WhatsAppGateway | None = None


def get_gateway() -> WhatsAppGateway:
    global _gateway
    if _gateway is None:
        from iler.config import get_settings
        from iler.whatsapp.evolution import EvolutionGateway

        _gateway = EvolutionGateway(get_settings())
    return _gateway


def set_gateway(gateway: WhatsAppGateway | None) -> None:
    """Replace the gateway (tests, fakes). `None` resets to the default Evolution gateway."""
    global _gateway
    _gateway = gateway
