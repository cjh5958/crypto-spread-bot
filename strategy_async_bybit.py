import asyncio
import ccxt.async_support as ccxt
from ccxt.base.errors import *
from collections import ChainMap
import datetime
import json
import functools
import os
import pandas as pd
from pandas import DataFrame
import logging
import time
import sys

# const
VERSION = 1.1
CONFIG_DIR = './configs'
LOG_FILE = './logs/strategy_log.txt'
DIVIDER = '=' * os.get_terminal_size().columns
SOURCE = 'close'

config: ChainMap = ChainMap()
for filename in ['exchange', 'strategy', 'trailing']:
    with open(f"{CONFIG_DIR}/{filename}_config.json", 'r') as F:
        config = ChainMap(config, json.load(F))

def get_logger():
    os.makedirs("logs", exist_ok=True)

    logger = logging.getLogger('logger')
    logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    file_handler = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)s] %(message)s", 
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

def round_price_to_tick(price, tick_size):
    tick_decimals = len(f"{tick_size:.10f}".rstrip('0').split('.')[1]) if '.' in f"{tick_size:.10f}" else 0
    adjusted_price = round(price / tick_size) * tick_size
    return f"{adjusted_price:.{tick_decimals}f}"

def convert_to_contract_coin(price, amount_usdt, min_amount, commission=0.00055, leverage=10) -> float:
    amount = float(min_amount)

    init_margin = amount * price / leverage
    open_fee = amount * price * commission
    close_fee_buy = amount * price * (1 - 1/leverage) * commission
    close_fee_sell = amount * price * (1 + 1/leverage) * commission
    
    cost =  max(init_margin + open_fee + close_fee_buy, init_margin + open_fee + close_fee_sell)

    return int((amount_usdt/cost)) * amount

def ema(data, period:int):
    df = pd.Series(data)
    ema = df.ewm(span=period, adjust=False).mean()
    return ema.iloc[-1]

def calculate_atr(klines, period=60):
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    atr = sum(trs[-period:]) / period
    return atr

def calculate_average_amplitude(klines, period=60):
    amplitudes = []
    for i in range(len(klines) - period, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        close = float(klines[i][4])
        amplitude = ((high - low) / close) * 100
        amplitudes.append(amplitude)
    average_amplitude = sum(amplitudes) / len(amplitudes)
    return average_amplitude

class Bot:
    def __init__(self, logger=None, *args, **kwargs):
        id = kwargs.get('id', None)

        try:
            bot_configuration = config["api"][id]
        except KeyError:
            self.exc = None
            raise

        self._exc = getattr(ccxt, id)(bot_configuration)
        if config["demo_trade"]:
            self._exc.enable_demo_trading(True)

        self._interval = float(config["monitor_interval"])

        self.logger = logger

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        _loop = asyncio.get_event_loop()
        try:
            _loop.run_until_complete(self.run())
        except KeyboardInterrupt :
            _loop.run_until_complete(self.close())
        # asyncio.run(self.run())

    async def close(self):
        await self._exc.close()

    async def run(self):
        while True:
            print(await self._get_ticker('BTC/USDT:USDT'))
            await asyncio.sleep(self._interval)

    async def _get_ticker(self, symbol: str):
        try:
            ticker = await self._exc.fetch_ticker(symbol)
            return ticker["ask"]
        except Exception as e:
            self.logger.error(f'獲取價格數據時發生錯誤：{e}')

    async def _fetch_klines(self, symbol: str, bar='1m', limit=241):
        try:
            klines = await self._exc.fetch_ohlcv(symbol, timeframe=bar, limit=limit)
            return klines
        except Exception as e:
            self.logger.error(f'獲取歷史數據時發生錯誤：{e}')

if __name__ == "__main__":
    Bot(get_logger(), id='bybit')