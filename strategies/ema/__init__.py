from strategies.base import register
from strategies.ema.strategy import EMAStrategy


@register("ema")
class RegisteredEMAStrategy(EMAStrategy):
    pass
