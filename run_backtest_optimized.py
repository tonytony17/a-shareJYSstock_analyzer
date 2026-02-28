#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
优化回测脚本 - 支持命令行参数（GitHub Actions 兼容版）
用法:
  单日回测: python run_backtest_optimized.py --mode single --date 2025-09-25 --hold 1
  多日回测: python run_backtest_optimized.py --mode multi --start 2025-09-23 --end 2025-09-27 --hold 1
"""

import sys
import os
import argparse

# 设置控制台编码为UTF-8
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import logging
import json
import time
import pickle

# 添加项目路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.data.data_fetcher import StockDataFetcher
from src.analysis.stock_filter import StockFilter
from config.backtest_config import BACKTEST_FILTER_CONFIG, BACKTEST_SAMPLE_CONFIG

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class OptimizedBacktest:
    """优化回测系统 - 使用宽松配置"""

    def __init__(self):
        self.data_fetcher = StockDataFetcher()
        self.stock_filter = StockFilter(config=BACKTEST_FILTER_CONFIG)
        self.cache_dir = './cache'
        os.makedirs(self.cache_dir, exist_ok=True)
        self.stock_name_cache = {}

    def get_cache_file(self, cache_key):
        return os.path.join(self.cache_dir, f"{cache_key}.pkl")

    def load_from_cache(self, cache_key):
        cache_file = self.get_cache_file(cache_key)
        if os.path.exists(cache_file):
            try:
                expire_days = BACKTEST_SAMPLE_CONFIG.get('cache_expire_days', 7)
                if time.time() - os.path.getmtime(cache_file) < (expire_days * 86400):
                    with open(cache_file, 'rb') as f:
                        return pickle.load(f)
            except:
                pass
        return None

    def save_to_cache(self, cache_key, data):
        cache_file = self.get_cache_file(cache_key)
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(data, f)
        except Exception as e:
            logger.warning(f"缓存保存失败: {e}")

    def get_stock_name(self, stock_code: str):
        if stock_code in self.stock_name_cache:
            return self.stock_name_cache[stock_code]
        try:
            info = ak.stock_individual_info_em(symbol=stock_code)
            if not info.empty:
                name = info[info['item'] == '股票简称']['value'].values
                if len(name) > 0:
                    stock_name = str(name[0])
                    self.stock_name_cache[stock_code] = stock_name
                    return stock_name
        except:
            pass
        return f'股票{stock_code}'

    def get_csi300_stocks(self):
        cache_key = "csi300_stocks_with_names"
        cached_data = self.load_from_cache(cache_key)
        if cached_data:
            logger.info(f"从缓存加载沪深300成分股: {len(cached_data.get('stocks', []))} 只")
            if isinstance(cached_data, dict):
                self.stock_name_cache = cached_data.get('name_cache', {})
                return cached_data.get('stocks', [])
            return cached_data

        stocks = []
        try:
            logger.info("正在获取沪深300成分股列表（方法1：akshare）...")
            csi300 = ak.index_stock_cons(symbol="000300")
            stocks = csi300['品种代码'].tolist()
            for i, code in enumerate(stocks):
                name = csi300[csi300['品种代码'] == code]['品种名称'].values
                if len(name) > 0:
                    self.stock_name_cache[code] = str(name[0])
            logger.info(f"✅ 成功获取 {len(stocks)} 只沪深300成分股")
        except Exception as e:
            logger.warning(f"⚠️ 方法1失败: {e}")
            try:
                csi300 = ak.index_stock_cons_csindex(symbol="000300")
                stocks = csi300['成分券代码'].tolist()
                logger.info(f"✅ 方法2成功获取 {len(stocks)} 只股票")
            except Exception as e2:
                logger.error(f"❌ 所有方法均失败: {e2}")

        if not stocks:
            return []

        cache_data = {'stocks': stocks, 'name_cache': self.stock_name_cache}
        self.save_to_cache(cache_key, cache_data)
        return stocks

    def get_stock_data_for_date(self, stock_code: str, date: str, pe_ratio: float = None, purpose: str = "buy"):
        cache_key = f"stock_{stock_code}_{date}_{purpose}"
        cached_data = self.load_from_cache(cache_key)
        if cached_data:
            if pe_ratio is not None and pe_ratio > 0:
                cached_data['pe_ratio'] = pe_ratio
            return cached_data

        try:
            end_date = (datetime.strptime(date, '%Y-%m-%d') + timedelta(days=5)).strftime('%Y%m%d')
            start_date = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=35)).strftime('%Y%m%d')

            df = ak.stock_zh_a_hist(symbol=stock_code, period="daily",
                                   start_date=start_date, end_date=end_date, adjust="qfq")
            if df.empty:
                return None

            df['日期'] = pd.to_datetime(df['日期'])
            target_date = pd.to_datetime(date)
            df_on_date = df[df['日期'] <= target_date]
            if df_on_date.empty:
                return None

            row = df_on_date.iloc[-1]
            stock_name = self.get_stock_name(stock_code)

            final_pe_ratio = 20.0
            if pe_ratio is not None and pe_ratio > 0:
                final_pe_ratio = pe_ratio
            elif '市盈率' in row:
                try:
                    pe_value = row['市盈率']
                    if pe_value and pe_value > 0:
                        final_pe_ratio = float(pe_value)
                except:
                    pass

            momentum_20d = 0
            if len(df_on_date) >= 20:
                momentum_20d = (row['收盘'] / df_on_date.iloc[-20]['收盘'] - 1) * 100
            elif len(df_on_date) >= 2:
                momentum_20d = (row['收盘'] / df_on_date.iloc[0]['收盘'] - 1) * 100

            data = {
                'code': stock_code,
                'name': stock_name,
                'price': float(row['收盘']),
                'change_pct': float(row['涨跌幅']),
                'volume': int(row['成交量']),
                'turnover': float(row['成交额']),
                'pe_ratio': final_pe_ratio,
                'momentum_20d': momentum_20d,
                'strength_score': 0
            }
            self.save_to_cache(cache_key, data)
            return data

        except Exception as e:
            logger.debug(f"获取 {stock_code} 数据失败: {e}")
            return None

    def get_next_trading_day(self, date: str, days: int = 1):
        try:
            current = datetime.strptime(date, '%Y-%m-%d')
            for i in range(1, 15):
                next_date = current + timedelta(days=i*days)
                if next_date.weekday() < 5:
                    return next_date.strftime('%Y-%m-%d')
            return (current + timedelta(days=days)).strftime('%Y-%m-%d')
        except:
            return date

    def fetch_pe_ratios_batch(self, stock_codes: list) -> dict:
        pe_dict = {}
        logger.info("📊 正在获取PE数据...")
        success_count = 0
        for i, code in enumerate(stock_codes):
            try:
                stock_data = self.data_fetcher.get_stock_realtime_data(code)
                if stock_data and stock_data.get('pe_ratio'):
                    pe_dict[code] = stock_data['pe_ratio']
                    success_count += 1
                if (i + 1) % 50 == 0:
                    logger.info(f"   进度: {i+1}/{len(stock_codes)}")
            except:
                pass
        logger.info(f"✅ 成功获取 {success_count}/{len(stock_codes)} 只股票的PE数据")
        return pe_dict

    def backtest_single_day(self, analysis_date: str, hold_days: int = 1):
        logger.info(f"\n{'='*70}")
        logger.info(f"📅 回测日期: {analysis_date} | 持有{hold_days}天")
        logger.info(f"{'='*70}")

        stock_list = self.get_csi300_stocks()
        if not stock_list:
            logger.error("无法获取沪深300成分股列表")
            return None

        sample_size = BACKTEST_SAMPLE_CONFIG['sample_size']
        if sample_size >= len(stock_list):
            sampled_stocks = stock_list
        else:
            import random
            random.seed(BACKTEST_SAMPLE_CONFIG['random_seed'])
            sampled_stocks = random.sample(stock_list, min(sample_size, len(stock_list)))

        logger.info(f"📊 分析股票数: {len(sampled_stocks)} 只")
        pe_ratios = self.fetch_pe_ratios_batch(sampled_stocks)

        stock_data = []
        for i, code in enumerate(sampled_stocks):
            if i % 20 == 0:
                logger.info(f"⏳ 数据获取进度: {i+1}/{len(sampled_stocks)}")
            data = self.get_stock_data_for_date(code, analysis_date, pe_ratios.get(code), "buy")
            if data:
                stock_data.append(data)
            if i % 20 == 19:
                time.sleep(0.3)

        logger.info(f"✅ 成功获取 {len(stock_data)} 只股票数据")

        if len(stock_data) < 10:
            logger.error("❌ 获取的股票数据太少")
            return None

        selected_stocks = self.stock_filter.select_top_stocks(stock_data)

        if not selected_stocks:
            logger.warning("⚠️  未筛选出任何股票")
            return {'analysis_date': analysis_date, 'selected_count': 0, 'performance': []}

        # 去重
        unique_stocks, seen_codes = [], set()
        for stock in selected_stocks:
            if stock['code'] not in seen_codes:
                unique_stocks.append(stock)
                seen_codes.add(stock['code'])
        selected_stocks = unique_stocks

        logger.info(f"\n🏆 筛选结果 ({len(selected_stocks)}只):")
        for stock in selected_stocks:
            logger.info(f"   #{stock['rank']} {stock['name']} ({stock['code']}): "
                       f"¥{stock['price']:.2f}, PE={stock.get('pe_ratio', 0):.1f}, "
                       f"分数={stock.get('strength_score', 0):.0f}")

        sell_date = self.get_next_trading_day(analysis_date, hold_days)
        logger.info(f"\n💰 收益计算 (卖出日期: {sell_date}):")

        performance = []
        avg_return = max_return = min_return = win_rate = win_count = 0

        for stock in selected_stocks:
            sell_data = self.get_stock_data_for_date(stock['code'], sell_date, purpose="sell")
            if sell_data:
                buy_price = stock['price']
                sell_price = sell_data['price']
                return_pct = (sell_price / buy_price - 1) * 100
                performance.append({
                    'code': stock['code'],
                    'name': stock['name'],
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'return_pct': return_pct,
                    'pe_ratio': stock.get('pe_ratio', 0),
                    'strength_score': stock.get('strength_score', 0)
                })
                emoji = "📈" if return_pct > 0 else "📉" if return_pct < 0 else "➖"
                logger.info(f"   {emoji} {stock['name']}: ¥{buy_price:.2f} → ¥{sell_price:.2f} ({return_pct:+.2f}%)")

        if performance:
            returns = [p['return_pct'] for p in performance]
            avg_return = sum(returns) / len(returns)
            max_return = max(returns)
            min_return = min(returns)
            win_count = len([r for r in returns if r > 0])
            win_rate = win_count / len(returns) * 100
            logger.info(f"\n📊 策略表现:")
            logger.info(f"   • 平均收益: {avg_return:+.2f}%")
            logger.info(f"   • 收益区间: {min_return:+.2f}% ~ {max_return:+.2f}%")
            logger.info(f"   • 胜率: {win_rate:.1f}% ({win_count}/{len(returns)})")

        return {
            'analysis_date': analysis_date,
            'sell_date': sell_date,
            'hold_days': hold_days,
            'selected_count': len(selected_stocks),
            'performance': performance,
            'summary': {
                'avg_return': avg_return,
                'max_return': max_return,
                'min_return': min_return,
                'win_rate': win_rate,
                'win_count': win_count,
                'total_count': len(performance)
            }
        }

    def backtest_multi_days(self, start_date: str, end_date: str, hold_days: int = 1):
        logger.info(f"\n{'='*70}")
        logger.info(f"📅 多日回测: {start_date} ~ {end_date}")
        logger.info(f"{'='*70}")

        trading_days = []
        current = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        while current <= end:
            if current.weekday() < 5:
                trading_days.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)

        logger.info(f"共 {len(trading_days)} 个交易日")

        all_results = []
        for i, date in enumerate(trading_days):
            logger.info(f"\n进度: {i+1}/{len(trading_days)}")
            result = self.backtest_single_day(date, hold_days)
            if result and result['selected_count'] > 0:
                all_results.append(result)
            time.sleep(1)

        if all_results:
            all_returns = []
            for r in all_results:
                all_returns.extend([p['return_pct'] for p in r['performance']])
            logger.info(f"\n{'='*70}")
            logger.info(f"📊 回测总结:")
            logger.info(f"   • 有效交易日: {len(all_results)}")
            logger.info(f"   • 总交易次数: {len(all_returns)}")
            if all_returns:
                win_count = len([r for r in all_returns if r > 0])
                logger.info(f"   • 整体平均收益: {sum(all_returns)/len(all_returns):+.2f}%")
                logger.info(f"   • 最佳单笔: {max(all_returns):+.2f}%")
                logger.info(f"   • 最差单笔: {min(all_returns):+.2f}%")
                logger.info(f"   • 整体胜率: {win_count/len(all_returns)*100:.1f}%")

        return all_results


def main():
    # ── 命令行参数解析（GitHub Actions 兼容）──────────────────
    parser = argparse.ArgumentParser(description='沪深300策略回测系统')
    parser.add_argument('--mode', choices=['single', 'multi'], default='single',
                        help='回测模式: single=单日, multi=多日')
    parser.add_argument('--date', type=str, default=None,
                        help='单日回测日期，格式: 2025-09-25')
    parser.add_argument('--start', type=str, default=None,
                        help='多日回测开始日期，格式: 2025-09-23')
    parser.add_argument('--end', type=str, default=None,
                        help='多日回测结束日期，格式: 2025-09-27')
    parser.add_argument('--hold', type=int, default=1,
                        help='持有天数，默认1天')
    args = parser.parse_args()

    print("="*70)
    print("📊 沪深300策略回测系统")
    print("="*70)

    backtest = OptimizedBacktest()
    os.makedirs('./logs/backtest', exist_ok=True)

    if args.mode == 'single':
        # 未传日期时默认用上一个工作日
        if args.date is None:
            today = datetime.today()
            offset = max(1, (today.weekday() + 6) % 7 - 3)  # 周一取上周五
            last_workday = today - timedelta(days=offset if today.weekday() == 0 else 1)
            args.date = last_workday.strftime('%Y-%m-%d')
            print(f"未指定日期，自动使用上一工作日: {args.date}")

        result = backtest.backtest_single_day(args.date, args.hold)
        if result:
            filename = f"./logs/backtest/backtest_{args.date}_{args.hold}days.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info(f"\n✅ 回测结果已保存: {filename}")

    elif args.mode == 'multi':
        if not args.start or not args.end:
            print("❌ 多日回测需要指定 --start 和 --end 参数")
            sys.exit(1)

        results = backtest.backtest_multi_days(args.start, args.end, args.hold)
        if results:
            filename = f"./logs/backtest/backtest_{args.start}_to_{args.end}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            logger.info(f"\n✅ 回测结果已保存: {filename}")


if __name__ == "__main__":
    main()
