import ccxt
from ccxt.base.errors import *
from collections import ChainMap
import datetime
import json
import os
import pandas as pd
from pandas import DataFrame
import logging
import telebot
import time

# const
VERSION = 1.1
CONFIG_DIR = './configs'
LOG_FILE = './logs/trail_log.txt'
DIVIDER = '=' * os.get_terminal_size().columns
SOURCE = 'close'

config: ChainMap = ChainMap()
for filename in ['exchange', 'strategy', 'trailing']:
    with open(f"{CONFIG_DIR}/{filename}_config.json", 'r') as F:
        config = ChainMap(config, json.load(F))

def create_logger():
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

logger = create_logger()

class Bot:
    def __init__(self):
        print(f'Trail Bot 初始化中...')
        print(f'CCXT API 版本: {ccxt.__version__}')

        try:
            self.exc = ccxt.bybit(config=config["api"]["bybit"])
            if config["demo_trade"]:
                self.exc.enable_demo_trading(True)

            self.tb = telebot.TeleBot(config["api"]["telegram"]["key"], parse_mode=None)
            self.chatid = config["api"]["telegram"]["chat_id"]
        
        except NetworkError as e:
            logger.error('Trade Bot初始化失敗：請重新檢查網路連線狀況')
            self.exc = None
            return
        
        except ValueError as e:
            logger.error(f"初始化 Telegram 機器人發生錯誤：檢查 'API KEY' 是否有誤")
            self.tb = None

        except Exception as e:
            logger.error(f'Trade Bot初始化失敗：{e}')
            self.exc = None
            return
        
        self.starttime = datetime.datetime.now()

        self.leverage = int(config["leverage"])
        self.stop_loss_pct = float(config["stop_loss_pct"])
        self.low_trail_stop_loss_pct = float(config["low_trail_stop_loss_pct"])
        self.trail_stop_loss_pct = float(config["trail_stop_loss_pct"])
        self.higher_trail_stop_loss_pct = float(config["higher_trail_stop_loss_pct"])
        self.low_trail_enable_threshold = float(config["low_trail_enable_threshold"])
        self.first_trail_enable_threshold = float(config["first_trail_enable_threshold"])
        self.second_trail_enable_threshold = float(config["second_trail_enable_threshold"])
        self.blacklist = set(config.get("blacklist", []))
        self.interval = float(config["monitor_interval"])

        self.highest_profits = {}
        self.current_tiers = {}
        self.detected_positions = set()

        logger.info(f'Trail Bot 版本 {VERSION} 開始運行')

    def monitor_position(self):
        positions = self._fetch_positions()

        for position in positions:
            pair:str = position['symbol']
            position_amt = float(position['info']['size'])  # 使用 positionAmt 来获取仓位数量
            entry_price = float(position['info']['avgPrice'])
            current_price = float(position['markPrice'])
            side:str = position['side']

            symbol = pair.split(':')[0]

            if position_amt == 0:
                continue

            if pair in self.blacklist:
                if not pair in self.detected_positions:
                    self.notify_telegram(f"檢測到封鎖名單：{symbol}，跳過監控")
                    self.detected_positions.add(pair)
                continue

            if not pair in self.detected_positions:
                self.detected_positions.add(pair)
                self.highest_profits[pair] = 0
                self.current_tiers[pair] = -1
                logger.info(f"首次檢測到倉位：{symbol}，數量：{position_amt}，入場價格：{entry_price}，方向：{side}")
                self.notify_telegram(f"🛑首次檢測到倉位\n\n幣種：{symbol}\n數量：{position_amt}\n入場價格：{entry_price}\n方向：{side.upper()}\n\n已重置檔位與最高獲利紀錄並開始監控")

            if side == 'long':
                profit_pct = (current_price - entry_price) / entry_price * 100
            elif side == 'short':
                profit_pct = (entry_price - current_price) / entry_price * 100
            else:
                continue

            highest_profit = self.highest_profits.get(pair, 0)
            if profit_pct > highest_profit:
                highest_profit = profit_pct
                self.highest_profits[pair] = highest_profit

            current_tier = self.current_tiers.get(pair, -1)
            if highest_profit >= self.second_trail_enable_threshold:
                current_tier = 2
            elif highest_profit >= self.first_trail_enable_threshold:
                current_tier = 1
            elif highest_profit >= self.low_trail_enable_threshold:
                current_tier = 0
            else:
                current_tier = -1

            self.current_tiers[pair] = current_tier

            print(DIVIDER)
            logger.info(f"監控 {symbol}，倉位大小：{position_amt}，方向：{side}，持倉價格：{entry_price}，標記價格：{current_price}，盈虧：{profit_pct:.2f}%，最高盈虧：{highest_profit:.2f}%，目前檔位：{current_tier}")

            if current_tier == 0:
                logger.info(f"價格回撤到 {self.low_trail_stop_loss_pct:.2f}% 離場")
                if profit_pct <= self.low_trail_stop_loss_pct:
                    logger.info(f"{symbol} 觸發低檔保護止盈，盈虧回撤到：{profit_pct:.2f}%，將平倉")
                    self._close_position(pair, abs(position_amt), 'sell' if side == 'long' else 'buy')
                    continue

            elif current_tier == 1:
                trail_stop_loss = highest_profit * (1 - self.trail_stop_loss_pct)
                logger.info(f"價格回撤到 {trail_stop_loss:.2f}% 後止盈")
                if profit_pct <= trail_stop_loss:
                    logger.info(f"{symbol} 價格達到獲利回徹閾值，目前檔位：第一檔移動止盈，最高盈虧：{highest_profit:.2f}%，目前盈虧：{profit_pct:.2f}%，平倉")
                    self._close_position(pair, abs(position_amt), 'sell' if side == 'long' else 'buy')
                    continue

            elif current_tier == 2:
                trail_stop_loss = highest_profit * (1 - self.higher_trail_stop_loss_pct)
                logger.info(f"價格回撤到 {trail_stop_loss:.2f}% 後止盈")
                if profit_pct <= trail_stop_loss:
                    logger.info(f"{symbol} 價格達到獲利回徹閾值，目前檔位：第二檔移動止盈，最高盈虧：{highest_profit:.2f}%，目前盈虧：{profit_pct:.2f}%，平倉")
                    self._close_position(pair, abs(position_amt), 'sell' if side == 'long' else 'buy')
                    continue

            if profit_pct <= -self.stop_loss_pct:
                logger.info(f"{symbol} 觸發止損，當前盈虧：{profit_pct:.2f}%，執行平倉")
                self._close_position(pair, abs(position_amt), 'sell' if side == 'long' else 'buy')

    def _close_position(self, pair:str, amount, side) -> bool:
        try:
            order = self.exc.create_order(pair, 'market', side, amount, None, {'type': 'future'})
            logger.info(f"{pair.split(':')[0]} 的持倉已關閉")
            self.notify_telegram(f"✅ {pair.split(':')[0]} 的持倉已關閉。")
            self.detected_positions.discard(pair)
            self.highest_profits.pop(pair, None)
            self.current_tiers.pop(pair, None)
            return True
        except Exception as e:
            logger.error(f"關閉 {pair} 持倉時發生錯誤：{e}")
            return False

    def _fetch_positions(self):
        try:
            positions = self.exc.fetch_positions()
            return positions
        except Exception as e:
            logger.error(f"獲取倉位資訊時發生錯誤：{e}")
            return []
    
    def notify_telegram(self, msg:str):
        if self.tb is not None:
            self.tb.send_message(self.chatid, msg)
            
    def run(self):
        if self.exc is None: return # Initialization failed.

        while True:
            try:
                self.monitor_position()
                time.sleep(self.interval)
            except KeyboardInterrupt as e:
                break
        
        # Formatting datetime info
        execution_time = datetime.datetime.now() - self.starttime
        days = execution_time.days
        hours, remainder = divmod(execution_time.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        print(DIVIDER)
        logger.info(f'Trail Bot 已結束運行')
        logger.info(f'運行時長：{days} 天 {hours} 小時 {minutes} 分鐘 {seconds} 秒')

if __name__ == "__main__":
    Bot().run()