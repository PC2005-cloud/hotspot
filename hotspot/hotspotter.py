"""
Hotspot — DeepSeek 热点搜索器

流程：读取 keywords.json → 逐个发给 DeepSeek → 获取 JSON 热点报告 → 保存
"""

import os
import json
import random
import logging
import time
from typing import List, Optional
import httpx

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(BASE_DIR, "results")

_RUN_DIR_CACHE = None

def get_run_dir() -> str:
    """返回当前运行目录：results/{日期}/{时间}/，同一次运行始终返回同一个目录"""
    global _RUN_DIR_CACHE
    if _RUN_DIR_CACHE is None:
        date = time.strftime("%Y-%m-%d")
        t = time.strftime("%H%M%S")
        _RUN_DIR_CACHE = os.path.join(RESULTS_DIR, date, t)
        os.makedirs(_RUN_DIR_CACHE, exist_ok=True)
    return _RUN_DIR_CACHE

def load_keywords() -> List[str]:
    return load_config().get("keywords", [])


def load_config() -> dict:
    """读取 config.json，返回配置字典"""
    path = os.path.join(BASE_DIR, "hotspot", "config.json")
    defaults = {"max_retries": 3, "headless": True}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 合并默认值，缺失的用默认
        for k, v in defaults.items():
            if k not in data:
                data[k] = v
        kw_count = len(data.get("keywords", []))
        logger.info(f"配置已加载: {path} ({kw_count} 个关键词, headless={data.get('headless', True)}, 最大重试={data.get('max_retries', 3)})")
        return data
    except FileNotFoundError:
        logger.error(f"配置文件不存在: {path}，使用默认配置")
        return defaults
    except json.JSONDecodeError as e:
        logger.error(f"配置文件 JSON 格式错误: {e}，使用默认配置")
        return defaults
    except Exception as e:
        logger.warning(f"读取 config.json 失败，使用默认配置: {e}")
        return defaults


def build_prompt(keyword: str) -> str:
    template = load_config().get("prompt", "")
    if not template:
        logger.warning(f"config.json 中未配置 prompt 模板，关键词 '{keyword}' 将无法搜索")
        return ""
    today = time.strftime("%Y-%m-%d")
    week_range = f"近7天内（{time.strftime('%m月%d日', time.localtime(time.time() - 7*86400))} 至 {time.strftime('%m月%d日')}）"
    return template.replace("{keyword}", keyword).replace("{today}", today).replace("{week_range}", week_range)


def parse_json_reply(text: str) -> Optional[dict]:
    """从 DeepSeek 回复中提取 JSON（去掉 markdown 包裹后解析）"""
    text = text.strip()
    if text.startswith("```json"):
        text = text[len("```json"):]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        import demjson3
        data = demjson3.decode(text, encoding="utf-8")
        if isinstance(data, dict):
            logger.info("JSON 解析成功（demjson3 宽松模式）")
            return data
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"demjson3 解析也失败: {e}")

    logger.warning("JSON 解析失败")
    return None


def save_result(keyword: str, data: dict) -> Optional[str]:
    """保存热点报告到 results/{日期}/{时间}/{keyword}.json，返回路径或 None"""
    run_dir = get_run_dir()
    path = os.path.join(run_dir, f"{keyword}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"已保存: {path}")
        return path
    except Exception as e:
        logger.error(f"保存结果文件失败 ({path}): {e}")
        return None


# ─── URL 验证 ──────────────────────────────────────

def _check_url(url: str, timeout: int = 8) -> str:
    """
    检测单个 URL 的状态，返回状态描述。

    返回值：
        "200"      - 正常可访问
        "30x"      - 重定向
        "40x"      - 客户端错误（如 404 Not Found）
        "500"      - 服务端错误
        "连接失败"  - DNS/网络错误
        "超时"     - 请求超时
        "错误"     - 其他异常
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
    }
    try:
        resp = httpx.head(url, headers=headers, timeout=timeout, follow_redirects=True)
        code = resp.status_code
    except httpx.ConnectError:
        return "连接失败"
    except httpx.TimeoutException:
        return "超时"
    except Exception:
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            code = resp.status_code
        except httpx.ConnectError:
            return "连接失败"
        except httpx.TimeoutException:
            return "超时"
        except Exception:
            return "错误"

    if code < 300:
        return str(code)
    elif code < 400:
        return "30x"
    elif code < 500:
        return str(code)
    else:
        return str(code)


def validate_urls(data: dict) -> dict:
    """
    验证结果中所有 URL 的可访问性。
    为每个 hotspot/观点 添加 url_status 字段。
    """
    if "hotspots" not in data:
        return data

    total = 0
    valid = 0
    invalid = 0
    not_found = 0

    for h in data["hotspots"]:
        url = h.get("url")
        if url and url != "null":
            total += 1
            status = _check_url(url)
            h["url_status"] = status
            if status in ("连接失败", "超时", "错误"):
                invalid += 1
                logger.warning(f"  [不可达] {h['title'][:40]} -> {url} ({status})")
            elif status == "404":
                not_found += 1
                logger.warning(f"  [404] {h['title'][:40]} -> {url}")
            else:
                valid += 1
        else:
            h["url_status"] = "无链接"

        for p in h.get("perspectives", []):
            p_url = p.get("url")
            if p_url and p_url != "null":
                total += 1
                status = _check_url(p_url)
                p["url_status"] = status
                if status in ("连接失败", "超时", "错误"):
                    invalid += 1
                elif status == "404":
                    not_found += 1
                else:
                    valid += 1
            else:
                p["url_status"] = "无链接"

    if total > 0:
        logger.info(f"URL 验证: {total} 个, {valid} 可用, {not_found} 个404, {invalid} 个不可达")
    else:
        logger.info("URL 验证: 无 URL 需要检测")

    return data


def _clean_status(data: dict):
    """从结果中移除 url_status 调试字段"""
    for h in data.get("hotspots", []):
        h.pop("url_status", None)
        h.pop("url_valid", None)
        for p in h.get("perspectives", []):
            p.pop("url_status", None)
            p.pop("url_valid", None)


def _match_reference_links(data: dict, links: list):
    """
    用引用链接填补无效 URL，同时清理摘要中的 -N / --N / -N- 标记。

    links 格式：[{"text": "-13", "url": "https://..."}, ...]
    """
    import re
    if not links or "hotspots" not in data:
        return

    # 构建引用字典 {"13": "https://...", "4": "https://..."}
    ref_map = {}
    for link in links:
        m = re.search(r'(\d+)', link.get("text", ""))
        if m:
            ref_map[m.group(1)] = link["url"]

    for h in data["hotspots"]:
        summary = h.get("summary", "")
        if not summary:
            continue

        # 查找所有 -N / --N / -N- 标记
        marker_map = {}  # {"13": "-13-", "2": "--2", ...}
        for m in re.finditer(r'-{1,2}(\d+)-?', summary):
            num = m.group(1)
            marker_map[num] = m.group(0)

        # 如果热点 URL 无效，尝试用引用链接填补
        url = h.get("url", "")
        if not url or url == "null":
            for num, marker in marker_map.items():
                if num in ref_map:
                    ref_url = ref_map[num]
                    logger.info(f"  引用链接填补: #{h['rank']} [{num}] {ref_url[:70]}")
                    h["url"] = ref_url
                    break

        # 清理摘要中的标记
        cleaned = summary
        for marker in marker_map.values():
            cleaned = cleaned.replace(marker, "")
        if cleaned != summary:
            logger.info(f"  摘要清理: #{h['rank']} 移除标记")
        h["summary"] = cleaned.strip()


def search_single(bot, keyword: str) -> dict:
    """搜索单个关键词（最多重试3次）。返回 {"keyword", "ok", "count", "elapsed", "path"}"""
    prompt = build_prompt(keyword)
    logger.info(f"\n{'='*50}")
    logger.info(f"  关键词: {keyword}")
    logger.info(f"{'='*50}")

    if not prompt:
        logger.error(f"  关键词 '{keyword}' prompt 为空，跳过搜索")
        return {"keyword": keyword, "ok": False, "count": 0, "elapsed": 0, "path": None}

    logger.info(f"  prompt 长度: {len(prompt)} 字")

    start = time.time()
    max_retries = load_config().get("max_retries", 3)
    reply_text = ""
    reply_links = []
    data = None

    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            # 指数退避 + 随机抖动
            backoff = (2 ** (attempt - 2)) * 10  # retry 2: 10s, retry 3: 20s
            jitter = random.uniform(5, 15)
            wait = backoff + jitter
            logger.info(f"  等待 {wait:.0f} 秒后重试 ({attempt}/{max_retries})...")
            time.sleep(wait)
            bot.new_chat()

        result = bot.chat(prompt, timeout=180)
        reply_text = result["text"]
        reply_links = result["links"]

        if not reply_text:
            logger.warning(f"  第{attempt}次尝试 — 未获取到回复")
            continue

        data = parse_json_reply(reply_text)
        if data:
            break

        # 保存失败现场
        debug_path = os.path.join(get_run_dir(), f"{keyword}_raw.txt")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(reply_text)
        logger.warning(f"  第{attempt}次尝试 — JSON 解析失败，原始回复已保存")

    elapsed = time.time() - start
    result = {"keyword": keyword, "ok": False, "count": 0, "elapsed": elapsed, "path": None}

    if not data:
        if not reply_text:
            logger.warning(f"  [失败] {keyword} — 重试{max_retries}次，均未获取到 AI 回复")
        else:
            logger.warning(f"  [失败] {keyword} — 重试{max_retries}次，JSON 解析均失败")
        return result

    # 用引用链接填补无效 URL + 清理摘要标记
    _match_reference_links(data, reply_links)

    # 去重：相同 URL 的热点只保留第一条
    before = len(data.get("hotspots", []))
    seen_urls = set()
    unique = []
    for h in data.get("hotspots", []):
        url = h.get("url", "")
        if url and url != "null":
            if url in seen_urls:
                continue
            seen_urls.add(url)
        unique.append(h)
    data["hotspots"] = unique
    after = len(data["hotspots"])
    if before != after:
        logger.info(f"  去重: {before - after} 条热点因 URL 重复已移除")

    logger.info("验证 URL 可用性...")
    data = validate_urls(data)

    before = len(data.get("hotspots", []))
    data["hotspots"] = [h for h in data.get("hotspots", []) if h.get("url_status", "").startswith("2") or h.get("url_status") == "30x"]
    after = len(data["hotspots"])
    if before != after:
        logger.info(f"  过滤: {before - after} 条热点因链接不可达已移除")

    # 添加搜索时间（由代码生成，不依赖 AI）
    data["start_time"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(start))
    data["end_time"] = time.strftime("%Y-%m-%d %H:%M")
    data["elapsed_seconds"] = round(elapsed)
    data["retry_count"] = attempt - 1

    _clean_status(data)
    path = save_result(keyword, data)

    result.update({"ok": True, "count": after, "path": path})
    logger.info(f"  [完成] {keyword} ({after} 条热点, {elapsed:.0f}秒)")

    return result


def aggregate_results(total_elapsed: float) -> Optional[str]:
    """聚合 results/ 下所有 JSON 文件为一份总报告"""
    import glob

    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "**", "*.json"), recursive=True))
    files = [f for f in files if os.path.basename(f) not in ("report.json", "report.html")]
    if not files:
        logger.warning("没有可聚合的结果文件，生成空报告")
        now = time.strftime("%Y-%m-%d %H:%M")
        report = {
            "report_time": now,
            "start_time": now,
            "end_time": now,
            "total_elapsed_seconds": round(total_elapsed),
            "total_keywords": 0,
            "success_count": 0,
            "failed_count": 0,
            "total_hotspots": 0,
            "results": [],
        }
        report_path = os.path.join(get_run_dir(), f"report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"空报告已保存: {report_path}")
        return report_path

    results = []
    total_hotspots = 0
    success = 0
    failed = 0

    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as e:
            logger.warning(f"文件 {f} JSON 格式错误: {e}，跳过")
            failed += 1
            continue
        except Exception as e:
            logger.warning(f"读取文件 {f} 失败: {e}，跳过")
            failed += 1
            continue

        hs = data.get("hotspots", [])
        count = len(hs)
        total_hotspots += count
        keyword = data.get("keyword", "?")
        if count > 0:
            success += 1
        else:
            failed += 1

        results.append({
            "keyword": keyword,
            "status": "完成" if count > 0 else "失败",
            "hotspots_count": count,
            "start_time": data.get("start_time", ""),
            "end_time": data.get("end_time", ""),
            "elapsed_seconds": data.get("elapsed_seconds", 0),
            "retry_count": data.get("retry_count", 0),
            "hotspots": hs,
            "public_sentiment_summary": data.get("public_sentiment_summary", ""),
            "honesty_note": data.get("honesty_note", ""),
        })

    now = time.strftime("%Y-%m-%d %H:%M")
    report = {
        "report_time": now,
        "start_time": results[0]["start_time"] if results else now,
        "end_time": results[-1]["end_time"] if results else now,
        "total_elapsed_seconds": round(total_elapsed),
        "total_keywords": len(files),
        "success_count": success,
        "failed_count": failed,
        "total_hotspots": total_hotspots,
        "results": results,
    }

    report_path = os.path.join(get_run_dir(), f"report.json")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"聚合报告已保存: {report_path}")
    except Exception as e:
        logger.error(f"保存聚合报告失败: {e}")
        return None
    return report_path


def generate_html_report(report_data: dict, output_path: str):
    """将聚合报告数据渲染为 HTML 文件"""
    def esc(text):
        if text is None:
            return ""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    d = report_data
    rd = d.get("results", [])
    total_kw = len(rd)
    done = sum(1 for r in rd if r.get("status") == "完成")
    total_hs = sum(len(r.get("hotspots", [])) for r in rd)

    tabs_html = ""
    for i, r in enumerate(rd):
        kw = esc(r.get("keyword", ""))
        hs_count = len(r.get("hotspots", []))
        active = "active" if i == 0 else ""
        tabs_html += f'<button class="tab {active}" data-tab="{i}">{kw} <span class="tab-count">{hs_count}</span></button>'

    panes_html = ""
    for i, r in enumerate(rd):
        hs = r.get("hotspots", [])
        cards = ""
        for h in hs:
            title = esc(h.get("title", ""))
            summary = esc(h.get("summary", ""))
            source = esc(h.get("source", ""))
            pub_time = esc(h.get("published_time", ""))
            url = h.get("url", "")
            rank = h.get("rank", "")
            pers = ""
            for p in h.get("perspectives", []):
                stance = esc(p.get("stance", ""))
                psum = esc(p.get("summary", ""))
                pers += f'<div class="persp"><span class="stance">{stance}</span>{psum}</div>'
            url_line = f'<div class="url-line"><a href="{url}" target="_blank">查看原文</a></div>' if url else ""
            cards += f"""
            <div class="card">
                <div class="card-header">
                    <span class="rank">#{rank}</span>
                    <span class="source">{source}</span>
                    <span class="ptime">{pub_time}</span>
                </div>
                <h2>{title}</h2>
                <p class="summary">{summary}</p>
                {url_line}
                {pers}
            </div>"""
        sentiment = esc(r.get("public_sentiment_summary", ""))
        active = "active" if i == 0 else ""
        panes_html += f"""
        <div class="tab-pane {active}" data-tab="{i}">
            <div class="pane-header">
                <div class="kw-meta">
                    <span>\u23f1 {r.get("elapsed_seconds","")}s</span>
                    <span>\U0001f550 {esc(r.get("start_time",""))}</span>
                    <span>\u91cd\u8bd5 {r.get("retry_count",0)} \u6b21</span>
                </div>
            </div>
            {cards}
            <div class="sentiment"><h3>\u8206\u60c5\u603b\u7ed3</h3><p>{sentiment}</p></div>
        </div>"""

    template_path = os.path.join(os.path.dirname(__file__), "template.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()
        logger.info(f"已加载 HTML 模板: {template_path}")
    except FileNotFoundError:
        logger.error(f"HTML 模板文件不存在: {template_path}，跳过 HTML 报告生成")
        return
    except Exception as e:
        logger.error(f"读取 HTML 模板失败: {e}，跳过 HTML 报告生成")
        return

    html = html.replace("{overview_time}", f'\U0001f550 {esc(d.get("start_time",""))} \u2014 {esc(d.get("end_time",""))}')
    html = html.replace("{overview_duration}", f'\u23f1 \u603b\u8017\u65f6 {d.get("total_elapsed_seconds","")}s')
    html = html.replace("{overview_count}", f'\U0001f4c4 {done}/{total_kw} \u6210\u529f \u00b7 {total_hs} \u6761\u70ed\u70b9')
    html = html.replace("{tabs}", tabs_html)
    html = html.replace("{panes}", panes_html)
    html = html.replace("{footer}", f"\u7531 Hotspot \u81ea\u52a8\u751f\u6210 \u00b7 {time.strftime('%Y-%m-%d %H:%M')}")

    if total_hs == 0:
        html = html.replace("{empty_reason}", "\u672c\u6b21\u8fd0\u884c\u672a\u641c\u7d22\u5230\u70ed\u70b9\u6570\u636e\u3002\u53ef\u80fd\u539f\u56e0\uff1a\u8d26\u53f7\u88ab\u5c01\u7981\u3001\u7f51\u7edc\u88ab\u62e6\u622a\u3001\u6216\u5173\u952e\u8bcd\u65e0\u5339\u914d\u7ed3\u679c\u3002")
    else:
        html = html.replace("{empty_reason}", "")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"HTML 报告已保存: {output_path}")

def run_all(bot) -> list:
    """遍历 keywords.json，逐个搜索。返回每个关键词的结果列表"""
    keywords = load_keywords()
    if not keywords:
        logger.warning("keywords.json 为空")
        return []

    logger.info(f"\n共 {len(keywords)} 个关键词，开始搜索...")
    stats = []
    for i, kw in enumerate(keywords):
        if i > 0:
            # 关键词间随机延迟，模拟人类操作间隔，防频率检测
            delay = random.uniform(15, 45)
            logger.info(f"  等待 {delay:.0f} 秒后处理下一个关键词...")
            time.sleep(delay)
            try:
                bot.new_chat()  # 每个关键词开新对话，避免上下文污染
            except Exception as e:
                logger.error(f"切换到新对话失败: {e}")
                # 继续尝试，不阻塞整体流程
        try:
            r = search_single(bot, kw)
        except Exception as e:
            logger.error(f"关键词 '{kw}' 搜索过程出现异常: {e}", exc_info=True)
            r = {"keyword": kw, "ok": False, "count": 0, "elapsed": 0, "path": None}
        stats.append(r)

    ok_count = sum(1 for s in stats if s["ok"])
    total_count = sum(s["count"] for s in stats)
    logger.info(f"搜索完成: {ok_count}/{len(keywords)} 成功, 共 {total_count} 条热点")
    return stats