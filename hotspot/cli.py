"""
Hotspot — 热点搜索器

自动读取 keywords.json，逐个搜索所有关键词，输出结果。
"""

import logging
import sys
import os
import time
from datetime import datetime

from .deepseek import DeepSeekBot
from .hotspotter import run_all, load_keywords, aggregate_results, load_config, generate_html_report, get_run_dir

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")


def setup_logging():
    """控制台输出 + 日志文件双输出"""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    log_file = os.path.join(get_run_dir(), "run.log")

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))

    logger.handlers.clear()
    logger.addHandler(console)
    logger.addHandler(fh)

    return log_file


def print_summary(stats, total_elapsed):
    ok_count = sum(1 for s in stats if s["ok"])
    total_count = sum(s["count"] for s in stats)

    lines = []
    lines.append("")
    lines.append("=" * 60)
    lines.append("  运行总结")
    lines.append("=" * 60)
    lines.append(f"  {'关键词':<20} {'状态':<8} {'热点数':<6} {'耗时':<6}")
    lines.append(f"  {'─'*20} {'─'*8} {'─'*6} {'─'*6}")
    for s in stats:
        status = "完成" if s["ok"] else "失败"
        count = str(s["count"]) if s["ok"] else "-"
        elapsed = f"{s['elapsed']:.0f}s"
        lines.append(f"  {s['keyword']:<20} {status:<8} {count:<6} {elapsed:<6}")
    lines.append("")
    lines.append(f"  总计: {ok_count}/{len(stats)} 成功 | {total_count} 条热点 | 共耗时 {total_elapsed:.0f}秒")
    lines.append("=" * 60)

    logger = logging.getLogger()
    for line in lines:
        logger.info(line)


def main():
    log_file = setup_logging()
    logger = logging.getLogger()
    start_time = time.time()

    logger.info("=" * 50)
    logger.info("  🔥 Hotspot — 热点搜索器")
    logger.info(f"  日志: {log_file}")
    logger.info("=" * 50)

    keywords = load_keywords()
    if not keywords:
        logger.error("❌ keywords.json 中没有配置任何关键词")
        sys.exit(1)

    logger.info(f"  关键词: {len(keywords)} 个 — {'、'.join(keywords)}")

    config = load_config()
    bot = DeepSeekBot(headless=config.get("headless", True))
    stats = []
    try:
        bot.start()
        cfg = load_config().get("login", {})
        login_ok = bot.login(cfg.get("account", ""), cfg.get("password", ""))
        if login_ok:
            bot.enable_all()
            stats = run_all(bot)
        else:
            logger.error("登录失败，跳过搜索")
            # 生成失败统计
            for kw in keywords:
                stats.append({
                    "keyword": kw, "ok": False, "count": 0,
                    "elapsed": 0, "path": None
                })
    finally:
        bot.close()

    total_elapsed = time.time() - start_time
    print_summary(stats, total_elapsed)

    # 聚合所有结果为 JSON + HTML 报告
    report_path = aggregate_results(total_elapsed)
    if report_path:
        import json
        with open(report_path, "r", encoding="utf-8") as f:
            report_data = json.load(f)
        html_path = report_path.replace(".json", ".html")
        generate_html_report(report_data, html_path)


if __name__ == "__main__":
    main()
