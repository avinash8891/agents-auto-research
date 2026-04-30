from strategies._demo.strategy import DemoStrategy
from strategies.base import register


@register("_demo")
class RegisteredDemoStrategy(DemoStrategy):
    pass
