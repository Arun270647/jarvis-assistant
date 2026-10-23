"""Price calculations for the demo shop."""

TAX_RATE = 0.18


def apply_discount(price: float, percent: float) -> float:
    """Price after a percentage discount, rounded to cents. percent is 0-100."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * percent / 100, 2)


def with_tax(price: float) -> float:
    """Price including tax, rounded to cents."""
    return round(price * (1 + TAX_RATE), 2)


def checkout_total(prices: list, percent: float = 0) -> float:
    """Sum of items, discounted, then taxed."""
    return with_tax(apply_discount(sum(prices), percent))
