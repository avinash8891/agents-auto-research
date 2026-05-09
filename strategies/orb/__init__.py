from strategies.base import register
from strategies.orb.strategy import ORBStrategy


@register("orb")
class RegisteredORBStrategy(ORBStrategy):
    pass
