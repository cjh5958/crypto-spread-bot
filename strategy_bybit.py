import ccxt
from ccxt.base.errors import *
from collections import ChainMap
import datetime
import json
import os
import pandas as pd
from pandas import DataFrame
import logging
import time

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

def round_price_to_tick(price, tick_size):
    tick_decimals = len(f"{tick_size:.10f}".rstrip('0').split('.')[1]) if '.' in f"{tick_size:.10f}" else 0
    adjusted_price = round(price / tick_size) * tick_size
    return f"{adjusted_price:.{tick_decimals}f}"

def convert_to_contrast_coin(price, amount_usdt, min_amount, commission=0.00055, leverage=10) -> float:
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
    def __init__(self):
        print(f'Trade Bot 初始化中...')
        print(f'CCXT API 版本: {ccxt.__version__}')

        try:
            self.exc = ccxt.bybit(config=config["api"]["bybit"])
            if config["demo_trade"]:
                self.exc.enable_demo_trading(True)

            self.pairs = [pair for pair in config["trading_pairs"]]
            for pair in self.pairs:
                l = self.exc.fetch_leverage(pair)
                if not int(l["info"]["leverage"]) == int(config["leverage"]):
                    self.exc.set_leverage(int(config["leverage"]), pair)

            self.interval = float(config["monitor_interval"])

            # account info
            self.initial_capital = self.capital = self.usdt
        
        except NetworkError as e:
            logger.error('Trade Bot初始化失敗：請重新檢查網路連線狀況')
            self.exc = None
            return
        except Exception as e:
            logger.error(f'Trade Bot初始化失敗：{e}')
            self.exc = None
            return
        
        self.starttime = datetime.datetime.now()

        logger.info(f'Trade Bot 版本-{VERSION} 開始運行')
        logger.info(f'帳戶初始資金：{self.capital:.3f} USDT')
        
    def run(self):
        if self.exc is None: return # Initialization failed.

        if len(self.pairs) == 0: return

        while True:
            try:
                for pair in self.pairs:
                    self._process_pair(pair, config["trading_pairs"][pair])
                time.sleep(self.interval)
            except KeyboardInterrupt as e:
                for pair in self.pairs:
                    self._cancel_all_orders(pair=pair)
                break
        
        # Formatting datetime info
        execution_time = datetime.datetime.now()-self.starttime
        days = execution_time.days
        hours, remainder = divmod(execution_time.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        profit = self.capital - self.initial_capital

        print(DIVIDER)
        logger.info(f'Trade Bot 已結束運行')
        logger.info(f'運行時長：{days} 天 {hours} 小時 {minutes} 分鐘 {seconds} 秒')
        logger.info(f'帳戶總收益：{profit:.3f} USDT({profit/self.initial_capital*100:.1f}%)')

    def _process_pair(self, pair:str, pair_config:dict):
        mark_price = self._get_current_price(pair)

        klines = self._fetch_kline_data(pair)
        if klines is None: return

        close_prices:list[float] = [float(kline[4]) for kline in klines]

        ema_value = int(pair_config.get('ema', 240))
        if ema_value == 0:
            is_bullish_trend = is_bearish_trend = True
        else:
            ema_trend = ema(close_prices, ema_value)
            # logger.info(f"{pair.split(':')[0]} EMA{ema_value}：{ema_trend:.6f} 現價：{mark_price:.6f}")
            is_bullish_trend = close_prices[-1] > ema_trend
            is_bearish_trend = close_prices[-1] < ema_trend

        atr = calculate_atr(klines)
        price_atr_ratio = (atr / mark_price) * 100

        average_amplitude = calculate_average_amplitude(klines)
        # logger.info(f"{pair.split(':')[0]} ATR：{atr} ATR ratio：{price_atr_ratio:.3f} % 平均振幅：{average_amplitude:.2f} %")

        value_multiplier = float(pair_config.get('value_multiplier', 2))
        selected_value = (average_amplitude + price_atr_ratio)/2 * value_multiplier

        long_price_factor = 1 - selected_value / 100
        short_price_factor = 1 + selected_value / 100

        target_price_long = mark_price * long_price_factor
        target_price_short = mark_price * short_price_factor

        long_amount_usdt = float(pair_config.get('long_amount_usdt', 20))
        short_amount_usdt = float(pair_config.get('short_amount_usdt', 20))

        self._cancel_all_orders(pair=pair)

        print(DIVIDER)

        if is_bullish_trend:
            logger.info(f"交易對 {pair.split(':')[0]} 確認為多頭趨勢，將掛入多單")
            self._place_order(pair, target_price_long, long_amount_usdt, 'buy')

        if is_bearish_trend:
            logger.info(f"交易對 {pair.split(':')[0]} 確認為空頭趨勢，將掛入空單")
            self._place_order(pair, target_price_short, short_amount_usdt, 'sell')

        
    def _get_current_price(self, pair:str) -> float:
        price = -1.0
        try:
            price = float(self.exc.fetch_ticker(pair)["ask"])
            return price
        except NetworkError as e:
            logger.error('獲取價格數據時發生網路異常：請重新檢查網路連線狀況')
            return price
        except RequestTimeout as e:
            logger.error('獲取價格數據時網路連線逾時：將重新嘗試')
            return self._get_current_price()
        except Exception as e:
            logger.error(f'獲取價格數據時發生未知錯誤：{e}')
            return price

    def _fetch_kline_data(self, pair:str, bar='1m', limit=241) -> list[list]:
        try:
            df = self.exc.fetch_ohlcv(pair, timeframe=bar, limit=limit)
            return df
        except NetworkError as e:
            logger.error('獲取歷史數據時發生網路異常：請重新檢查網路連線狀況')
        except Exception as e:
            logger.error(f'獲取歷史數據時發生錯誤：{e}')
        return None
    
    def _place_order(self, pair:str, price, amount_usdt, side):
        try:
            market = self.exc.load_markets(True)
            min_amount = float(market[pair]['limits']['amount']['min'])
            leverage = int(config["leverage"])
            amount = convert_to_contrast_coin(price, amount_usdt, min_amount, 0.0002, leverage)

            if amount == 0:
                logger.info(f"下單保證金低於最低名義價值：請增加單次下單保證金。")
                return

            logger.info(f"掛單價格：{price:.6f}")
            logger.info(f"掛單手數：{amount:.8f}")
            logger.info(f"槓桿：{leverage}x")

            order = self.exc.create_order(
                symbol=pair, 
                type='limit', 
                side=side, 
                amount=amount, 
                price=price
            )

            logger.info(f"成功掛入訂單，訂單ID：{order['id']}")

        except InsufficientFunds as e:
            logger.error(f"保證金不足，無法提交訂單")

        except BadRequest as e:
            logger.error(f"提交訂單時發生錯誤：{e}")

        except Exception as e:
            logger.error(f"提交訂單時發生錯誤：{type(e)}")
            print(e)

    def _cancel_all_orders(self,/,pair:str):
        try:
            orders = self.exc.fetch_open_orders(pair, params={"orderFilter": "Order"})
            for order in orders:
                orderId = order["id"]
                self.exc.cancel_order(orderId, pair)
        except Exception as e:
            logger.error(f'{e}')

    @property
    def usdt(self) -> float:
        try:
            balance = self.exc.fetch_balance()["info"]["result"]["list"][0]["coin"]
        except NetworkError as e:
            return -1
        except RequestTimeout as e:
            return -1
        except Exception as e:
            return -1
        
        for coin in balance:
            if coin["coin"] == "USDT":
                return float(coin["equity"])

if __name__ == "__main__":
    Bot().run()