# -*- coding: utf-8 -*-
"""
网络爬虫模块。

两个引擎，同一个接口：
  * SeleniumFetcher —— 无头 Chrome 打开桌面版 baike.baidu.com。
                       百度百科对纯 HTTP 客户端一律返回 403（百度安全验证），
                       只有真实浏览器能拿到完整词条，这是首选引擎。
  * RequestsFetcher —— 直接请求移动版 wapbaike.baidu.com（服务器端渲染，
                       移动 UA 可直接拿到 200 与完整正文），快且省资源。

对外只暴露 crawl_all()：读人物列表 -> 并发抓取 -> 断点续爬 -> 落盘。
"""
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import config as C
from utils import (build_url, get_logger, ignore_proxy, load_names, now_str,
                   sleep_a_bit, human_size)

log = get_logger()

# 判定「词条不存在」的特征串
_NOT_FOUND_MARKS = ("百度百科尚未收录", "该词条不存在", "您搜索的词条尚未收录")
# 判定「被反爬拦截」的特征串
_BLOCKED_MARKS = ("百度安全验证", "请开启JavaScript并刷新该页")
# 判定「多义词（消歧义）」的特征串
_DISAMB_MARKS = ("本词条是一个多义词", "这是一个多义词", "多义词，请在下列义项中选择浏览")


@dataclass
class FetchResult:
    name: str
    url: str
    status: str = "ok"          # ok / not_found / disambiguation_failed / blocked / timeout / error
    html: str = ""
    final_url: str = ""
    title: str = ""
    engine: str = ""
    error: str = ""
    elapsed: float = 0.0
    attempts: int = 1
    crawl_time: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.html)


# --------------------------------------------------------------------------- #
# Selenium 引擎
# --------------------------------------------------------------------------- #
class SeleniumFetcher:
    """每线程一个浏览器实例；实例按需创建、按需回收。"""

    def __init__(self):
        self._local = threading.local()
        ignore_proxy()
        try:
            from selenium import webdriver  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "未安装 selenium，请先执行：pip install selenium"
            ) from e

    # -- 浏览器生命周期 ----------------------------------------------------- #
    def _make_driver(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        for a in (
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-notifications",
            "--disable-popup-blocking",
            "--blink-settings=imagesEnabled=false",   # 不加载图片，省流量提速
            "--window-size=1400,1000",
            "--lang=zh-CN",
            "--log-level=3",
        ):
            opts.add_argument(a)
        opts.add_argument(f"--user-agent={random.choice(C.USER_AGENTS)}")
        if C.IGNORE_PROXY:
            opts.add_argument("--no-proxy-server")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.page_load_strategy = "eager"

        drv = webdriver.Chrome(options=opts)
        drv.set_page_load_timeout(C.PAGE_TIMEOUT)
        drv.implicitly_wait(0)
        return drv

    @property
    def driver(self):
        drv = getattr(self._local, "driver", None)
        if drv is None:
            drv = self._make_driver()
            self._local.driver = drv
            self._local.count = 0
        self._local.count += 1
        if self._local.count % C.RECYCLE_EVERY == 0:
            # 长跑容易攒下内存，定期重开一个干净的浏览器
            self._quit()
            drv = self._make_driver()
            self._local.driver = drv
        return drv

    def _quit(self):
        drv = getattr(self._local, "driver", None)
        if drv is not None:
            try:
                drv.quit()
            except Exception:
                pass
            self._local.driver = None

    def close(self):
        self._quit()

    # -- 抓取 ---------------------------------------------------------------- #
    def fetch(self, name: str, url: str = None) -> FetchResult:
        from selenium.common.exceptions import TimeoutException, WebDriverException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        url = url or build_url(name, mobile=False)
        res = FetchResult(name=name, url=url, engine="selenium")
        t0 = time.time()
        try:
            drv = self.driver
            try:
                drv.get(url)
            except TimeoutException:
                # 页面反复有长连接未结束，DOM 通常已经就绪，继续读源码
                pass

            try:
                WebDriverWait(drv, C.PAGE_TIMEOUT).until(
                    lambda d: d.execute_script(
                        "return !!document.querySelector("
                        "'[class*=J-lemma-content],[class*=J-basic-info],"
                        "[class*=contentItemChild],[class*=lemmaTitle]')"
                    )
                )
            except TimeoutException:
                pass
            time.sleep(random.uniform(0.6, 1.2))   # 等 React 补完剩余节点

            html = drv.page_source
            res.title = drv.title or ""
            res.final_url = drv.current_url

            if any(m in html for m in _BLOCKED_MARKS):
                res.status, res.error = "blocked", "命中百度安全验证页"
                return res
            if any(m in html for m in _NOT_FOUND_MARKS):
                res.status, res.error = "not_found", "词条不存在或尚未收录"
                return res

            # 多义词页：跟到第一个义项
            if any(m in html for m in _DISAMB_MARKS):
                sub = self._pick_disambiguation(drv, html)
                if not sub:
                    res.status, res.error = "disambiguation_failed", "多义词页无可用义项"
                    return res
                drv.get(sub)
                try:
                    WebDriverWait(drv, C.PAGE_TIMEOUT).until(
                        lambda d: d.execute_script(
                            "return !!document.querySelector("
                            "'[class*=J-lemma-content],[class*=J-basic-info]')"
                        )
                    )
                except TimeoutException:
                    pass
                time.sleep(random.uniform(0.6, 1.2))
                html = drv.page_source
                res.title = drv.title or ""
                res.final_url = drv.current_url
                res.extra["resolved_from_disambiguation"] = sub

            if not html or len(html) < 5000:
                res.status, res.error = "not_found", "页面内容过短，判为未收录"
                return res

            res.html = html
            res.status = "ok"
        except WebDriverException as e:
            msg = str(e).splitlines()[0][:200]
            if "timeout" in msg.lower():
                res.status, res.error = "timeout", f"页面超时：{msg}"
            else:
                res.status, res.error = "error", f"浏览器异常：{msg}"
        except Exception as e:  # pragma: no cover
            res.status, res.error = "error", f"{type(e).__name__}: {e}"
        finally:
            res.elapsed = time.time() - t0
        return res

    @staticmethod
    def _pick_disambiguation(drv, html: str) -> Optional[str]:
        """多义词页取第一个义项链接。"""
        try:
            els = drv.find_elements(
                "css selector", "a[class*=contentItemChild][href*='/item/']"
            )
            if els:
                return els[0].get_attribute("href")
        except Exception:
            pass
        # 退回静态解析
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            for a in soup.select("a[href*='/item/']"):
                if "contentItemChild" in " ".join(a.get("class") or []):
                    href = a.get("href")
                    if href:
                        return href if href.startswith("http") else \
                            "https://baike.baidu.com" + href
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------- #
# requests 引擎（移动版）
# --------------------------------------------------------------------------- #
class RequestsFetcher:
    def __init__(self):
        import requests  # noqa: F401
        self._local = threading.local()

    @property
    def session(self):
        import requests
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.trust_env = not C.IGNORE_PROXY   # 关键：避免本机请求被塞给代理
            s.headers.update({
                "User-Agent": C.MOBILE_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://wapbaike.baidu.com/",
            })
            self._local.session = s
        return s

    def close(self):
        s = getattr(self._local, "session", None)
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
            self._local.session = None

    def fetch(self, name: str, url: str = None) -> FetchResult:
        import requests
        url = url or build_url(name, mobile=True)
        res = FetchResult(name=name, url=url, engine="requests")
        t0 = time.time()
        try:
            r = self.session.get(url, timeout=C.PAGE_TIMEOUT, allow_redirects=True)
            html = r.text or ""
            res.final_url = r.url
            if r.status_code == 403 or any(m in html for m in _BLOCKED_MARKS):
                res.status, res.error = "blocked", f"HTTP {r.status_code} 被拦截"
                return res
            if r.status_code == 404 or any(m in html for m in _NOT_FOUND_MARKS):
                res.status, res.error = "not_found", f"HTTP {r.status_code} 词条不存在"
                return res
            if r.status_code != 200:
                res.status, res.error = "error", f"HTTP {r.status_code}"
                return res
            if len(html) < 5000:
                res.status, res.error = "not_found", "页面内容过短，判为未收录"
                return res
            res.html, res.status = html, "ok"
        except requests.exceptions.Timeout:
            res.status, res.error = "timeout", "请求超时"
        except Exception as e:
            res.status, res.error = "error", f"{type(e).__name__}: {e}"
        finally:
            res.elapsed = time.time() - t0
        return res


ENGINES = {"selenium": SeleniumFetcher, "requests": RequestsFetcher}


def _name_from_url(url: str) -> str:
    from urllib.parse import unquote
    m = re.search(r"/item/([^/?#]+)", url)
    return unquote(m.group(1)) if m else ""


def _swap_host(url: str, mobile: bool) -> str:
    """
    把 URL 换到对应的站点版本（只换主机名，路径原样保留）。

    requests 引擎请求桌面版 baike.baidu.com 必被「百度安全验证」拦，
    所以引擎与域名必须配套：selenium<->桌面版，requests<->移动版。
    用户直接粘桌面版 URL 又选了 requests 时，这里自动换到移动版。
    """
    if not url.startswith("http"):
        return url
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit(url)
    host = "wapbaike.baidu.com" if mobile else "baike.baidu.com"
    if p.netloc == host:
        return url
    return urlunsplit((p.scheme, host, p.path, p.query, p.fragment))


def fetch_one(url_or_name: str, engine: str = None) -> FetchResult:
    """
    抓取单个页面，供 GUI 使用。传入完整 URL 或人名均可。

    与 crawl_all 的区别：这里用完立刻关掉浏览器，不常驻，
    免得 GUI 一直挂着一个 Chrome 占内存与端口。
    """
    engine = (engine or C.DEFAULT_ENGINE).lower()
    if engine not in ENGINES:
        raise ValueError(f"未知引擎 {engine}，可选：{list(ENGINES)}")
    fetcher = ENGINES[engine]()
    try:
        text = (url_or_name or "").strip()
        if text.startswith("http"):
            text = _swap_host(text, mobile=(engine == "requests"))
            name = _name_from_url(text) or text
            r = fetcher.fetch(name, url=text)
        else:
            name = text
            r = fetcher.fetch(text)
        r.name = r.name or name
        r.crawl_time = r.crawl_time or now_str()
        return r
    finally:
        fetcher.close()


# --------------------------------------------------------------------------- #
# 调度
# --------------------------------------------------------------------------- #
def _fetch_with_retry(fetcher, name: str) -> FetchResult:
    """失败重试；指数退避。"""
    last = None
    for attempt in range(1, C.MAX_RETRIES + 1):
        r = fetcher.fetch(name)
        r.attempts = attempt
        last = r
        if r.ok or r.status in ("not_found", "disambiguation_failed"):
            return r            # 这两类是确定性失败，重试没意义
        if attempt < C.MAX_RETRIES:
            wait = C.RETRY_BACKOFF ** attempt + random.uniform(0, 1.0)
            log.warning("%s 第 %d 次失败（%s），%.1fs 后重试", name, attempt, r.error, wait)
            time.sleep(wait)
    return last


def crawl_all(names=None, engine: str = None, workers: int = None,
              limit: int = None, resume: bool = True, on_result=None,
              on_progress=None):
    """
    批量抓取。

    参数
    ----
    on_result : 可调用对象，收到 FetchResult（此时 r.html 仍然完整）。
                调用方应当在这里立刻把 HTML 解析掉并落盘，因为回调返回后
                本函数就会清空 r.html 释放内存 —— 2000 页桌面版全留在内存里
                会直接把机器撑爆。
    on_progress : 可调用对象 (finished, total, result)，用于进度条。

    返回 (results, failed)：
        results —— 成功条目（HTML 已被清空，仅保留元数据）
        failed  —— 失败条目（含 status / error / attempts）
    成功条目的元数据追加写入 logs/crawl_index.jsonl，失败写入
    logs/failed.jsonl。断点续爬依据 data/persons.jsonl 里已完成的。
    """
    engine = (engine or C.DEFAULT_ENGINE).lower()
    workers = workers or C.DEFAULT_WORKERS
    if engine not in ENGINES:
        raise ValueError(f"未知引擎 {engine}，可选：{list(ENGINES)}")

    names = list(names) if names else load_names()
    if limit:
        names = names[:limit]

    done = set()
    if resume:
        from utils import read_jsonl
        done = {row["name"] for row in read_jsonl(C.PERSONS_JSONL) if row.get("name")}
    todo = [n for n in names if n not in done]

    log.info("引擎=%s 并发=%d 名单=%d 已完成=%d 待抓取=%d",
             engine, workers, len(names), len(done), len(todo))

    fetcher = ENGINES[engine]()
    results, failed = [], []
    lock = threading.Lock()
    total = len(todo)
    finished = 0
    t_start = time.time()

    def work(name):
        try:
            return _fetch_with_retry(fetcher, name)
        finally:
            sleep_a_bit()

    try:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="crawler") as pool:
            futures = {pool.submit(work, n): n for n in todo}
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    r = fut.result()
                except Exception as e:                      # pragma: no cover
                    r = FetchResult(name=name, url=build_url(name),
                                    status="error", error=f"{type(e).__name__}: {e}")
                r.crawl_time = r.crawl_time or now_str()
                with lock:
                    finished += 1
                    if r.ok:
                        size = len(r.html)
                        from utils import save_html
                        save_html(r.name, r.html)
                        from utils import append_jsonl
                        append_jsonl({
                            "name": r.name, "url": r.url, "engine": r.engine,
                            "final_url": r.final_url, "title": r.title,
                            "html_size": size, "elapsed": round(r.elapsed, 2),
                            "attempts": r.attempts, "crawl_time": r.crawl_time,
                            "extra": r.extra,
                        }, C.CRAWL_INDEX)
                        if on_result:
                            # 交给调用方立即解析落盘
                            on_result(r)
                        r.html = ""          # 关键：及时释放，避免内存堆积
                        results.append(r)
                        log.info("[%d/%d] OK   %s (%.1fs, %s)",
                                 finished, total, name, r.elapsed, human_size(size))
                    else:
                        failed.append(r)
                        from utils import FAILED_JSONL, append_jsonl
                        append_jsonl({
                            "name": r.name, "url": r.url, "engine": r.engine,
                            "status": r.status, "error": r.error,
                            "attempts": r.attempts, "time": now_str(),
                        }, FAILED_JSONL)
                        log.warning("[%d/%d] FAIL %s -> %s (%s)",
                                    finished, total, name, r.status, r.error)
                    if on_progress:
                        on_progress(finished, total, r)
    finally:
        fetcher.close()
        snap = _progress_snapshot(names, results, failed, t_start)
        from utils import dump_json
        dump_json(snap, C.PROGRESS_JSON)

    ok_rate = (len(results) / total * 100) if total else 0
    log.info("抓取结束：成功 %d / %d（%.1f%%），失败 %d，耗时 %.1fs",
             len(results), total, ok_rate, len(failed), time.time() - t_start)
    return results, failed


def _progress_snapshot(names, results, failed, t_start):
    from utils import load_json, read_jsonl
    ok_names = sorted({row["name"] for row in read_jsonl(C.PERSONS_JSONL)
                       if row.get("name")})
    fail_map = {}
    for row in read_jsonl(C.FAILED_JSONL):
        fail_map[row.get("name")] = row.get("status")
    return {
        "updated": now_str(),
        "engine": (results[0].engine if results else C.DEFAULT_ENGINE),
        "name_total": len(names),
        "ok": len(ok_names),
        "failed": len(fail_map),
        "remaining": max(len(names) - len(ok_names) - len(fail_map), 0),
        "ok_rate": round(len(ok_names) / len(names), 4) if names else 0,
        "failed_detail": fail_map,
        "last_run_seconds": round(time.time() - t_start, 1),
    }
