#!/usr/bin/env python
"""
StockBot

https://github.com/shirosaidev/stockbot

Copyright (C) Chris Park (shirosai) 2021
stockbot is released under the Apache 2.0 license. See
LICENSE for the full license text.
"""

import sys
import csv
import requests
import time
import optparse
import math
from requests import ReadTimeout, ConnectTimeout, HTTPError, Timeout, ConnectionError
from datetime import date, datetime, timedelta
from pytz import timezone
from random import randint
from urllib.parse import urlparse

from config import *

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

STOCKBOT_VERSION = '0.1.0'
__version__ = STOCKBOT_VERSION

TZ = timezone('America/New_York')
alpaca = tradeapi.REST(APIKEYID, APISECRETKEY, APIBASEURL)


def get_stock_df(stock):
    try:
        data = alpaca.get_barset(stock, 'day', limit=5).df
        df = data[stock]
        # df['pct_change'] = round(((df['close'] - df['open']) / df['open']) * 100, 4)
        # df['net_change'] = 1 + (df['pct_change'] / 100)
        # df['cum_change'] = df['net_change'].cumprod()
        return df

    except (ConnectTimeout, HTTPError, ReadTimeout, Timeout, ConnectionError) as e:
        print('{}: Alpaca API CONNECTION ERROR: {}'.format(stock, e))
        time.sleep(randint(2, 5))
        get_stock_df(stock)


def get_current_price(symbol):
    try:
        return alpaca.get_last_trade(symbol).price
    except (ConnectTimeout, HTTPError, ReadTimeout, Timeout, ConnectionError) as e:
        print('{}: Alpaca API CONNECTION ERROR: {}'.format(symbol, e))
        time.sleep(randint(2, 5))
        get_current_price(symbol)


def get_closed_orders(startbuytime):
    if startbuytime == 'buyatclose':
        datestamp = date.today() - timedelta(days=1)
    else:
        datestamp = datetime.today().date()
    return alpaca.list_orders(
        status='closed',
        limit=100,
        after=str(datestamp)
    )


def get_eod_change_percents(startbuytime):
    orders = get_closed_orders(startbuytime)
    todays_buy_sell = {}
    for order in orders:
        if order.symbol not in todays_buy_sell:
            todays_buy_sell[order.symbol] = {'buy': 0, 'sell': 0, 'change': 0}
        if order.side == 'sell':
            todays_buy_sell[order.symbol]['sell'] += int(order.filled_qty) * float(order.filled_avg_price)
        elif order.side == 'buy':
            todays_buy_sell[order.symbol]['buy'] += int(order.filled_qty) * float(order.filled_avg_price)
    for ticker in todays_buy_sell:
        todays_buy_sell[ticker]['change'] = int(round((todays_buy_sell[ticker]['sell'] - todays_buy_sell[ticker]['buy'])
                                                      / todays_buy_sell[ticker]['buy'] * 100, 2))
        todays_buy_sell[ticker]['sell'] = round(todays_buy_sell[ticker]['sell'], 2)
        todays_buy_sell[ticker]['buy'] = round(todays_buy_sell[ticker]['buy'], 2)
    return todays_buy_sell


def get_nasdaq_buystocks():
    # api used by https://www.nasdaq.com/market-activity/stocks/screener
    url = NASDAQ_API_URL
    parsed_uri = urlparse(url)
    # stagger requests to avoid connection issues to nasdaq.com
    time.sleep(randint(1, 3))
    headers = {
        'authority': parsed_uri.netloc,
        'method': 'GET',
        'scheme': 'https',
        'path': parsed_uri.path + '?' + parsed_uri.params,
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
        'accept-encoding': 'gzip, deflate, br',
        'accept-laguage': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'pragma': 'no-cache',
        'sec-fetch-dest': 'document',
        'sec-fetch-site': 'none',
        'sec-fetch-mode': 'navigate',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36'
    }
    try:
        return requests.get(url, headers=headers).json()
    except (ConnectTimeout, HTTPError, ReadTimeout, Timeout, ConnectionError) as e:
        print('NASDAQ CONNECTION ERROR: {}'.format(e))
        time.sleep(randint(2, 5))
        get_nasdaq_buystocks()


def get_num_shares(symbol):
    try:
        current_price = alpaca.get_last_trade(symbol).price
        print('{} was last traded at ${}'.format(symbol, str(current_price)))
        return math.floor(INVESTMENT_PER_TRADE / current_price)
    except (ConnectTimeout, HTTPError, ReadTimeout, Timeout, ConnectionError, APIError) as e:
        print('{}: Alpaca CONNECTION ERROR while getting num of shares: {}'.format(symbol, e))
        time.sleep(randint(1, 3))
        get_num_shares(symbol)


def alpaca_order(symbol, side, _type='market', time_in_force='day'):
    num_shares = get_num_shares(symbol)
    try:
        print("{}: Placing order for {} shares".format(symbol, str(num_shares)))
        alpaca.submit_order(
            symbol=symbol,
            qty=num_shares,
            side=side,
            type=_type,
            time_in_force=time_in_force
        )
    except (ConnectTimeout, HTTPError, ReadTimeout, Timeout, ConnectionError, APIError) as e:
        print('{}: Alpaca error while placing order: {}'.format(symbol, e))
        time.sleep(randint(1, 3))
        alpaca_order(symbol, side)


def calculate_weightage(moved, change_low_to_market):
    return moved + (change_low_to_market * 2)


def select_best(biggest_movers):
    return [x for x in biggest_movers if x["weightage"] > 10 and x["today_change"] > 4][:MAX_NUM_STOCKS]


def get_todays_picks():
    # get the best buy and strong buy stock from Nasdaq.com and
    # sort them by the best stocks using one of the chosen algo

    print(datetime.now(tz=TZ).isoformat())
    print('getting buy and strong buy stocks from Nasdaq.com...')

    data = get_nasdaq_buystocks()
    stock_info = []

    nasdaq_records = data['data']['table']['rows']
    for count, record in enumerate(nasdaq_records):

        stock = record['symbol'].upper()
        if not alpaca.get_asset(stock).tradable:
            print('stock symbol {} is not tradable with Alpaca'.format(stock))
            continue

        stock_price = get_current_price(stock)
        if stock_price > STOCK_MAX_PRICE or stock_price < STOCK_MIN_PRICE:
            continue

        sys.stdout.write('[{}/{}] -> '.format(count + 1, len(nasdaq_records)))
        sys.stdout.flush()

        df = get_stock_df(stock)
        price_open = df.iloc[-MOVED_DAYS]['open']
        price_close = df.iloc[-1]['close']
        percent_change = round((price_close - price_open) / price_open * 100, 3)

        print('{} moved {}% over the last {} days'.format(record['symbol'], percent_change, MOVED_DAYS))

        latest_record = df.iloc[-1]
        stock_open = latest_record['open']
        stock_high = latest_record['high']
        stock_low = latest_record['low']
        stock_close = latest_record['close']
        stock_volume = latest_record['volume']

        today_change = round((stock_close - stock_open) * 100 / stock_open, 3)
        weightage = calculate_weightage(percent_change, today_change)

        stock_info.append({'symbol': stock, 'company': record['name'], 'market_price': stock_price,
                           'low': stock_low, 'high': stock_high, 'volume': stock_volume,
                           'moved': percent_change, 'today_change': today_change, 'weightage': weightage
                           })

    biggest_movers = sorted(stock_info, key=lambda i: i['weightage'], reverse=True)
    stock_picks = select_best(biggest_movers)
    print('\n', datetime.now(tz=TZ).isoformat())
    print('today\'s picks {}'.format(stock_picks))
    print('\n')
    return stock_picks


def main():
    usage = """Usage: stockbot.py [-h] [-b startbuytime]

StockBot v{0}
Alpaca algo stock trading bot.""".format(STOCKBOT_VERSION)
    parser = optparse.OptionParser(usage=usage)

    parser.add_option('-b', '--startbuytime', default='buyatclose',
                      help='when to starting buying stocks, options are buyatopen, and buyatclose, default "%default"')
    options, args = parser.parse_args()

    # print banner
    banner = """\033[32m                                
    _____ _           _   _____     _   
    |   __| |_ ___ ___| |_| __  |___| |_ 
    |__   |  _| . |  _| '_| __ -| . |  _|
    |_____|_| |___|___|_,_|_____|___|_|  
    StockBot v{0}    +$ = :)  -$ = :(\n
    Credits to: https://github.com/shirosaidev/stockbot\033[0m\n\n""".format(STOCKBOT_VERSION)

    print(banner)

    startbuytime = options.startbuytime

    print('Buy time: {}'.format(startbuytime))

    # Get our account information.
    account = alpaca.get_account()

    print('Account info:')
    print(account)

    # Check if our account is restricted from trading.
    if account.trading_blocked:
        print('Account is currently restricted from trading.')
        sys.exit(0)

    # List current positions
    print('Current positions:')
    print(alpaca.list_positions())

    equity = START_EQUITY

    # times to buy/sell

    if startbuytime == 'buyatopen':
        buy_sh, buy_sm = BAO_BUY_START_TIME.split(':')
        buy_eh, buy_em = BAO_BUY_END_TIME.split(':')
        sell_sh, sell_sm = BAO_SELL_START_TIME.split(':')
        sell_eh, sell_em = BAO_SELL_END_TIME.split(':')
    else:  # buy at close
        buy_sh, buy_sm = BAC_BUY_START_TIME.split(':')
        buy_eh, buy_em = BAC_BUY_END_TIME.split(':')
        sell_sh, sell_sm = BAC_SELL_START_TIME.split(':')
        sell_eh, sell_em = BAC_SELL_END_TIME.split(':')

    stock_bought_prices = []
    bought_stocks = []

    while True:
        try:
            # buy stocks

            # buy at open
            # check stock prices at 9:30am EST (market open) and continue to check for the next 1.5 hours
            # to see if stock is going down or going up, when the stock starts to go up, buy

            # buy at close
            # buy stocks at 3:00pm EST and hold until next day

            if datetime.today().weekday() in [0, 1, 2, 3, 4] \
                    and datetime.now(tz=TZ).hour == int(buy_sh) \
                    and datetime.now(tz=TZ).minute == int(buy_sm):

                stock_picks = get_todays_picks()
                print(datetime.now(tz=TZ).isoformat())
                print('starting to buy stocks...')

                stock_prices = []
                total_buy_price = 0

                while True:
                    for stock in stock_picks:
                        already_bought = False
                        for stockval in stock_bought_prices:
                            if stockval[0] == stock['symbol']:
                                already_bought = True
                                break
                        if already_bought:
                            continue

                        stock_price_buy = get_current_price(stock['symbol'])

                        # count the number of stock prices for the stock we have
                        num_prices = 0
                        went_up = 0
                        went_down = 0
                        for stockitem in stock_prices:
                            if stockitem[0] == stock['symbol']:
                                num_prices += 1
                                # check prev. price compared to now to see if it went up or down
                                if stock_price_buy > stockitem[1]:
                                    went_up += 1
                                else:
                                    went_down += 1

                        # buy the stock if there are 5 records of it and it's gone up and if we have
                        # enough equity left to buy
                        # if buying at end of day, ignore record checking to force it to buy

                        if startbuytime == 'buyatclose':
                            n = 0
                            went_up = 1
                            went_down = 0
                        else:
                            n = 5
                        buy_price = stock_price_buy * get_num_shares(stock['symbol'])
                        if num_prices >= n and went_up > went_down and equity >= buy_price:
                            buy_time = datetime.now(tz=TZ).isoformat()
                            print(buy_time)
                            alpaca_order(stock['symbol'], side='buy')
                            print('placed buy order of stock {} ({}) for ${} (vol {})'.format(
                                stock['symbol'], stock['company'], stock_price_buy, stock['volume']))
                            total_buy_price += buy_price
                            stock_bought_prices.append([stock['symbol'], stock_price_buy, buy_time])
                            bought_stocks.append(stock)
                            equity -= buy_price

                        stock_prices.append([stock['symbol'], stock_price_buy])

                    # sleep and check prices again after 2 min if time is before 11:00am EST / 4:00pm EST (market close)
                    if len(stock_bought_prices) == MAX_NUM_STOCKS or \
                            equity == 0 or \
                            (datetime.now(tz=TZ).hour == buy_eh and datetime.now(tz=TZ).minute >= buy_em):
                        break
                    else:
                        time.sleep(120)

                print(datetime.now(tz=TZ).isoformat())
                print('sent buy orders for {} stocks, market price ${}'.format(len(bought_stocks),
                                                                               round(total_buy_price, 2)))
                if startbuytime == 'buyatclose':
                    print('holding these stocks and selling them the next market open day...')
                print('\n')

            # sell stocks

            # check stock prices at 9:30am EST (buy at close) / 11:00am EST and continue to check until 1:00pm EST to
            # see if it goes up by x percent, sell it if it does
            # when the stock starts to go down starting at 1:00pm EST, sell or 
            # sell at end of day 2:00pm EST (buy at close) / 3:30pm EST

            if datetime.today().weekday() in [0, 1, 2, 3, 4] \
                    and datetime.now(tz=TZ).hour == int(sell_sh) \
                    and datetime.now(tz=TZ).minute >= int(sell_sm):

                stock_prices = []
                stock_sold_prices = []
                stock_data_csv = [['symbol', 'company', 'buy', 'buy time', 'sell', 'sell time',
                                   'profit', 'percent', 'vol sod']
                                  ]

                print(datetime.now(tz=TZ).isoformat())
                print('selling stock if it goes up by {}%...'.format(SELL_PERCENT_GAIN))

                while True:
                    for stock in bought_stocks:
                        already_sold = False
                        for stockval in stock_sold_prices:
                            if stockval[0] == stock['symbol']:
                                already_sold = True
                                break
                        if already_sold:
                            continue

                        stock_price_sell = get_current_price(stock['symbol'])

                        stockinfo = [x for x in stock_bought_prices if x[0] is stock['symbol']]
                        stock_price_buy = stockinfo[0][1]
                        buy_time = stockinfo[0][2]

                        # sell the stock if it's gone up by x percent
                        change_perc = round((stock_price_sell - stock_price_buy) / stock_price_buy * 100, 2)
                        sell_time = datetime.now(tz=TZ).isoformat()
                        diff = round(stock_price_sell - stock_price_buy, 2)
                        if change_perc >= SELL_PERCENT_GAIN:
                            print(sell_time)
                            alpaca_order(stock['symbol'], side='sell')
                            print('placed sell order of stock {} ({}) for ${} (diff ${} {}%)'.format(
                                stock['symbol'], stock['company'], stock_price_sell, diff, change_perc))
                            sell_price = stock_price_sell * get_num_shares(stock['symbol'])
                            stock_data_csv.append([stock['symbol'], stock['company'], stock_price_buy, buy_time,
                                                   stock_price_sell, sell_time, diff, change_perc, stock['volume']])
                            stock_sold_prices.append([stock['symbol'], stock_price_sell, sell_time])
                            equity += sell_price
                        else:
                            print(sell_time)
                            print('stock {} ({}) hasn\'t gone up enough to sell ${} (diff ${} {}%)'.format(
                                stock['symbol'], stock['company'], stock_price_sell, diff, change_perc))

                    # sleep and check prices again after 2 min if time is before 1:00pm EST
                    if len(stock_sold_prices) == len(bought_stocks) or \
                            (datetime.now(tz=TZ).hour == 13 and datetime.now(tz=TZ).minute >= 0):  # 1:00pm EST
                        break
                    else:
                        time.sleep(120)

                if len(stock_sold_prices) < len(bought_stocks) and \
                        (datetime.now(tz=TZ).hour == 13 and datetime.now(tz=TZ).minute >= 0):  # 1:00pm EST

                    print(datetime.now(tz=TZ).isoformat())
                    print('selling any remaining stocks if they go down, or else sell at end of day...')

                    while True:
                        for stock in bought_stocks:
                            already_sold = False
                            for stockval in stock_sold_prices:
                                if stockval[0] == stock['symbol']:
                                    already_sold = True
                                    break
                            if already_sold:
                                continue

                            stock_price_sell = get_current_price(stock['symbol'])

                            # count the number of stock prices for the stock we have
                            num_prices = 0
                            went_up = 0
                            went_down = 0
                            for stockitem in stock_prices:
                                if stockitem[0] == stock['symbol']:
                                    num_prices += 1
                                    # check prev. price compared to now to see if it went up or down
                                    if stock_price_sell > stockitem[1]:
                                        went_up += 1
                                    else:
                                        went_down += 1

                            stock_prices.append([stock['symbol'], stock_price_sell])

                            # sell the stock if there are 15 records of it and it's gone down
                            # or sell if it's the end of the day
                            if (num_prices >= 15 and went_down > went_up) or \
                                    (datetime.now(tz=TZ).hour == sell_eh and datetime.now(tz=TZ).minute >= sell_em):
                                stockinfo = [x for x in stock_bought_prices if x[0] is stock['symbol']]
                                stock_price_buy = stockinfo[0][1]
                                buy_time = stockinfo[0][2]
                                diff = round(stock_price_sell - stock_price_buy, 2)
                                change_perc = round((stock_price_sell - stock_price_buy) / stock_price_buy * 100, 2)
                                sell_time = datetime.now(tz=TZ).isoformat()
                                print(sell_time)
                                alpaca_order(stock['symbol'], side='sell')
                                print('placed sell order of stock {} ({}) for ${} (diff ${} {}%)'.format(
                                    stock['symbol'], stock['company'], stock_price_sell, diff, change_perc))
                                sell_price = stock_price_sell * get_num_shares(stock['symbol'])
                                stock_data_csv.append([stock['symbol'], stock['company'], stock_price_buy, buy_time,
                                                       stock_price_sell, sell_time, diff, change_perc, stock['volume']])
                                stock_sold_prices.append([stock['symbol'], stock_price_sell, sell_time])
                                equity += sell_price

                        # sleep and check prices again after 2 min if time is before # 3:30pm EST / 2:30pm EST (buy at close)
                        if len(stock_sold_prices) == len(bought_stocks) or \
                                (datetime.now(tz=TZ).hour == sell_eh and datetime.now(tz=TZ).minute >= sell_em):
                            break
                        time.sleep(120)

                    # sold all stocks or market close

                    percent = round((equity - START_EQUITY) / START_EQUITY * 100, 2)
                    equity = round(equity, 2)
                    print(datetime.now(tz=TZ).isoformat())
                    print('*** PERCENT {}%'.format(percent))
                    print('*** EQUITY ${}'.format(equity))

                    # wait until end of day for all the final sells and
                    # print an Alpaca stock summary
                    print('waiting for Alpaca report...')
                    while True:
                        if datetime.now(tz=TZ).hour == sell_eh and datetime.now(tz=TZ).minute >= sell_em + 5:
                            break
                        time.sleep(60)

                    # print out summary of today's buy/sells on alpaca

                    todays_buy_sell = get_eod_change_percents(startbuytime)
                    print(datetime.now(tz=TZ).isoformat())
                    print(todays_buy_sell)
                    print('********************')
                    print('TODAY\'S PROFIT/LOSS')
                    print('********************')
                    total_profit = 0
                    total_buy = 0
                    total_sell = 0
                    n = 0
                    stock_data_csv.append([])
                    stock_data_csv.append(['symbol', 'buy', 'sell', 'change'])
                    for k, v in todays_buy_sell.items():
                        change_str = '{}{}'.format('+' if v['change'] > 0 else '', v['change'])
                        print('{} {}%'.format(k, change_str))
                        stock_data_csv.append([k, v['buy'], v['sell'], v['change']])
                        total_profit += v['change']
                        total_buy += v['buy']
                        total_sell += v['sell']
                        n += 1
                    print('-------------------')
                    sum_str = '{}{}%'.format('+' if v['change'] > 0 else '', round(total_profit, 2))
                    avg_str = '{}{}%'.format('+' if v['change'] > 0 else '', round(total_profit / n, 2))
                    buy_str = '${}'.format(round(total_buy, 2))
                    sell_str = '${}'.format(round(total_sell, 2))
                    profit_str = '${}'.format(round(total_sell - total_buy, 2))
                    print('*** SUM {}'.format(sum_str))
                    print('*** AVG {}'.format(avg_str))
                    print('*** BUY {}'.format(buy_str))
                    print('*** SELL {}'.format(sell_str))
                    print('*** PROFIT/LOSS {}'.format(profit_str))

                    # write csv

                    now = datetime.now(tz=TZ).date().isoformat()
                    csv_file = 'stocks_{0}_{1}.csv'.format(startbuytime, now)
                    f = open(csv_file, 'w')

                    with f:
                        writer = csv.writer(f)
                        for row in stock_data_csv:
                            writer.writerow(row)
                        writer.writerow([])
                        writer.writerow(["PERCENT", percent])
                        writer.writerow(["EQUITY", equity])
                        writer.writerow([])
                        writer.writerow(["SUM", sum_str])
                        writer.writerow(["AVG", avg_str])
                        writer.writerow([])
                        writer.writerow(["BUY", buy_str])
                        writer.writerow(["SELL", sell_str])

                    # set equity back to start value to not reinvest any gains
                    if equity > START_EQUITY:
                        equity = START_EQUITY

            print(datetime.now(tz=TZ).isoformat(), '$ zzz...')
            time.sleep(60)

        except KeyboardInterrupt:
            print('Ctrl+c pressed, exiting')
            exit(0)


if __name__ == "__main__":
    main()
