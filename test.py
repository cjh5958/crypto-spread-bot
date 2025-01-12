def round_price_to_tick(price, tick_size):
    tick_decimals = len(f"{tick_size:.10f}".rstrip('0').split('.')[1]) if '.' in f"{tick_size:.10f}" else 0
    adjusted_price = round(price / tick_size) * tick_size
    return f"{adjusted_price:.{tick_decimals}f}"

n = 59876.1234
print(round_price_to_tick(n, .9))