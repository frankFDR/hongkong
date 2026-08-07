# -*- coding: utf-8 -*-
"""抓取、解析并验证越南 NSO/GSO 纺织品月度进出口数据（单文件、独立运行）。

与旧版的区别：
  * 通过 **越南住宅 IP** 直取真站 nso.gov.vn（不再主要依赖 web.archive.org 快照），
    覆盖 sources_manifest 里全部年份 + 网站新进数据。
  * 越南代理参数拆到同目录的 ``config.yaml``，不再硬编码；改配置即可换账号/国家/会话。
  * 内置一个进程内本地转发线程：对 ``*.gov.vn`` 做「本地解析成真 IP、只把 IP 发给
    IPRoyal」，绕过 IPRoyal 对越南政府域名的黑名单；其它域名走远程解析。
  * 抓数据前做连通性预检；连不上时按原因分级提示（网关不通 / 认证失败 / 余额流量用尽）。

本文件不依赖项目内其他 Python 文件；第三方依赖：``xlrd==2.0.1``、``PyYAML``、``pypdf``。
下载文件缓存在 ``cache/vietnam/``，最终输出默认 ``output/Vietnam.csv``。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import secrets
import select
import socket
import ssl
import struct
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path


HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache" / "vietnam"
DEFAULT_OUTPUT = HERE / "output" / "Vietnam.csv"
DEFAULT_CONFIG = HERE / "config.yaml"
CUSTOMS_LIST_URL = "https://www.customs.gov.vn/bridge?url=/customs/api/GetTKHQInfo"
OUTPUT_COLUMNS = ["年月", "年", "月", "进口额", "出口额"]
DETAIL_COLUMNS = [
    "source_page", "source_file", "year", "month", "flow",
    "commodity_original", "commodity_normalized", "value",
    "source_value", "source_unit",
]

SOURCE_PAGES = {
    2015: "https://www.nso.gov.vn/en/data-and-statistics/2019/11/preliminary-exports-and-imports-in-2015/",
    2016: "https://www.nso.gov.vn/en/data-and-statistics/2019/12/exports-and-imports-value-by-months-of-2016/",
    2017: "https://www.nso.gov.vn/en/data-and-statistics/2019/12/13663/",
    2018: "https://www.nso.gov.vn/en/data-and-statistics/2019/11/preliminary-exports-and-imports-in-2018/",
    2019: "https://www.nso.gov.vn/en/data-and-statistics/2019/11/preliminary-exports-and-imports-of-goods-by-main-countries-and-territories-in-2019/",
    2020: "https://www.nso.gov.vn/en/data-and-statistics/2020/10/preliminary-exports-and-imports-of-goods-by-main-countries-and-territories-in-2020/",
}

KNOWN_FILES = {
    2015: ("E1-2015.xls", "E2-2015.xls"),
    2016: ("Exports-by-months-of-2016.xls", "Imports-by-months-of-2016.xls"),
    2017: ("E1-2017.xls", "E2-2017.xls"),
    2018: ("E1-2018.xls", "E2-2018.xls"),
    2019: ("E1-2019-3.xls", "E2-2019-4.xls"),
    2020: ("E01-2020-4.xls", "E02-2020-4.xls"),
    2021: ("E01-2021-10.xls", "E02-2021-10.xls"),
    2022: ("E01-2022-11.xls", "E02-2022-11.xls"),
    2023: ("E01-2023-8.xls", "E02-2023-8.xls"),
    2024: ("E01-2024-7.xls", "E02-2024-7.xls"),
    2025: ("E01-2025-10.xls", "E02-2025-10.xls"),
    2026: ("E01-2026-3.xls", "E02-2026-3.xls"),
}

# 标签按贸易方向精确匹配（键为 clean() 归一化后的官方英文标签）。
FLOW_LABELS = {
    "export": {
        "fibres not spun": "fibres_not_spun",
        "fibres of all kinds": "fibres_all_kinds",
        "yarn": "yarn",
        "textile sewing products": "textiles_and_garments_or_sewing_products",
        "textiles and garments": "textiles_and_garments",
        "pieces of cloth and other technical cloths": "technical_cloths",
        "tyre cord fabrics and other fabrics for technical uses": "technical_fabrics",
        "tyre cord fabric and other woven fabrics": "technical_or_woven_fabrics",
        "auxiliary materials for textile garment leather footwear":
            "textile_garment_leather_footwear_materials_auxiliaries",
        "auxiliary materials for textile garment footgear":
            "textile_garment_footgear_materials_auxiliaries",
        "textile leather and foot wear materials and auxiliaries":
            "textile_leather_footwear_materials_auxiliaries",
        "other made up textile articles": "other_made_up_textile_articles",
    },
    "import": {
        "cotton": "cotton",
        "fibres not spun": "fibres_not_spun",
        "fibres spun": "fibres_spun",
        "yarn": "yarn",
        "textile fibrics": "fabrics",
        "textile fabrics": "fabrics",
        "fabrics": "fabrics",
        "auxiliary materials for textile garment leather footwear":
            "textile_garment_leather_footwear_materials_auxiliaries",
        "auxiliary materials for textile garment footgear":
            "textile_garment_footgear_materials_auxiliaries",
        "textile leather and foot wear materials and auxiliaries":
            "textile_leather_footwear_materials_auxiliaries",
    },
}

MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# Vietnam Customs tables 14B/15B use exactly these headline commodity rows.
# Their reporting-month values reproduce the NSO E01/E02 textile totals.
CUSTOMS_MONTHLY_LABELS = {
    "export": (
        "Yarn",
        "Textiles and garments",
        "Tyre cord fabrics and other fabrics for technical uses",
        "Textile, leather and foot-wear materials and auxiliaries",
        "Other made up textile articles",
    ),
    "import": (
        "Cotton",
        "Yarn",
        "Fabrics",
        "Textile, leather and foot-wear materials and auxiliaries",
    ),
}

# gov 域名本地解析的兜底 IP（DoH 失败时使用；官方站点 IP 变动时可更新）。
GOV_IP_FALLBACK = {"nso.gov.vn": "160.25.148.3", "www.nso.gov.vn": "160.25.148.3"}


# ==========================================================================
# 配置
# ==========================================================================
class VietnamProxyError(RuntimeError):
    """越南代理相关的可读错误（连通性预检失败时抛出）。"""


def _error_detail(exc: BaseException) -> str:
    """保留网络异常的真实类型和嵌套原因，避免只剩笼统的“无响应”。"""
    details = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip() or repr(current)
        details.append(f"{type(current).__name__}: {text}")
        reason = getattr(current, "reason", None)
        current = reason if isinstance(reason, BaseException) else current.__cause__
    return " <- ".join(details)


def load_config(path: Path) -> dict:
    if not path.exists():
        raise VietnamProxyError(
            f"缺少配置文件 {path}。请在 automated_data/ 下放一个 config.yaml"
            "（可参考仓库自带的示例）。"
        )
    try:
        import yaml
    except ImportError as exc:
        raise VietnamProxyError("缺少依赖 PyYAML，请运行: python -m pip install pyyaml") from exc
    with path.open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}
    cfg.setdefault("vietnam_proxy", {})
    cfg.setdefault("preflight", {})
    return cfg


# ==========================================================================
# 进程内越南转发（IPRoyal 住宅，gov 域名做 IP 替换绕过黑名单）
# ==========================================================================
class VietnamRelay:
    """本地 HTTP CONNECT 转发，上游 IPRoyal 越南住宅出口。

    浏览器/urllib 把 HTTP(S) 代理指向 ``http://127.0.0.1:<local_port>`` 即可。
    """

    def __init__(self, cfg: dict):
        self.host = cfg.get("upstream_host", "geo.iproyal.com")
        self.port = int(cfg.get("upstream_port", 12321))
        self.username = cfg.get("username", "")
        self.local_port = int(cfg.get("local_port", 18080))
        self.password = self._assemble_password(cfg)
        self._dns_cache: dict[str, str] = {}
        self._dns_source: dict[str, str] = {}
        self._srv: socket.socket | None = None
        self._last_relay_error: str | None = None

    @staticmethod
    def _assemble_password(cfg: dict) -> str:
        pw = str(cfg.get("password", ""))
        country = str(cfg.get("country", "") or "").strip()
        city = str(cfg.get("city", "") or "").strip()
        if country:
            pw += f"_country-{country}"
        if city:
            pw += f"_city-{city}"
        if cfg.get("sticky_session"):
            session = secrets.token_hex(4)
            lifetime = str(cfg.get("session_lifetime", "30m") or "30m")
            pw += f"_session-{session}_lifetime-{lifetime}"
        return pw

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    # ---- 上游 SOCKS5 ----
    def _socks5_connect(self, dst_host: str, dst_port: int, send_as_ip: bool,
                        timeout: float = 30.0) -> socket.socket:
        s = socket.create_connection((self.host, self.port), timeout=min(20.0, timeout))
        s.settimeout(timeout)
        try:
            s.sendall(b"\x05\x01\x02")
            head = s.recv(2)
            if len(head) < 2 or head[1] != 0x02:
                raise VietnamProxyError("PROXY_NO_AUTH")
            u, p = self.username.encode(), self.password.encode()
            s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
            ok = s.recv(2)
            if len(ok) < 2 or ok[1] != 0x00:
                raise VietnamProxyError("PROXY_AUTH_FAILED")
            if send_as_ip:
                req = b"\x05\x01\x00\x01" + socket.inet_aton(dst_host)
            else:
                hb = dst_host.encode()
                req = b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb
            req += struct.pack(">H", dst_port)
            s.sendall(req)
            resp = s.recv(4)
            if len(resp) < 2:
                raise VietnamProxyError("PROXY_NO_REPLY")
            if resp[1] != 0x00:
                raise VietnamProxyError(f"PROXY_CONNECT_RC={resp[1]}")
            atyp = resp[3]
            if atyp == 1:
                s.recv(6)
            elif atyp == 3:
                s.recv(s.recv(1)[0] + 2)
            elif atyp == 4:
                s.recv(18)
            return s
        except Exception:
            s.close()
            raise

    def _doh_query(self, host: str) -> tuple[str | None, str | None]:
        ip = None
        try:
            req = urllib.request.Request(
                f"https://1.1.1.1/dns-query?name={host}&type=A",
                headers={"accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=15,
                                        context=ssl.create_default_context()) as r:
                for ans in json.load(r).get("Answer", []):
                    if ans.get("type") == 1:
                        ip = ans["data"]
                        break
            if not ip:
                return None, "DoH 返回成功，但响应中没有 A 记录"
            return ip, None
        except Exception as exc:  # noqa: BLE001 诊断信息由调用方展示
            return None, _error_detail(exc)

    def _doh_resolve(self, host: str) -> str | None:
        if host in self._dns_cache:
            return self._dns_cache[host]
        ip, _ = self._doh_query(host)
        source = "Cloudflare DoH"
        if not ip:
            # NSO addresses change. Prefer the host's current system-DNS answer over
            # a static emergency address when DoH is unavailable.
            try:
                ip = socket.gethostbyname(host)
                source = "系统 DNS"
            except OSError:
                ip = GOV_IP_FALLBACK.get(host)
                source = "代码内置备用 IP"
        if ip:
            self._dns_cache[host] = ip
            self._dns_source[host] = source
        return ip

    @staticmethod
    def _is_gov(host: str) -> bool:
        return host == "gov.vn" or host.endswith(".gov.vn")

    def _open_upstream(self, host: str, port: int, timeout: float = 30.0) -> socket.socket:
        if self._is_gov(host):
            ip = self._doh_resolve(host)
            if not ip:
                raise VietnamProxyError(f"无法解析 {host} 的真实 IP")
            return self._socks5_connect(ip, port, send_as_ip=True, timeout=timeout)
        return self._socks5_connect(host, port, send_as_ip=False, timeout=timeout)

    # ---- 本地监听 ----
    def start(self) -> "VietnamRelay":
        # 配置端口被占时自动顺延(同进程内预检的监听 fd 偶见被子进程继承而残留,
        # 如 WSL/容器环境),最多尝试 20 个端口;proxy_url 按最终端口动态生成。
        base_port, last_exc = self.local_port, None
        for offset in range(20):
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                srv.bind(("127.0.0.1", base_port + offset))
            except OSError as exc:
                srv.close()
                last_exc = exc
                continue
            self.local_port = base_port + offset
            if offset:
                print(f"Vietnam 转发: 端口 {base_port} 被占用,改用 {self.local_port}")
            break
        else:
            raise VietnamProxyError(
                f"本地端口 {base_port}-{base_port + 19} 均无法监听：{last_exc}；"
                "可在 config.yaml 改 local_port。"
            ) from last_exc
        srv.listen(128)
        self._srv = srv
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass
            self._srv = None

    def _serve(self) -> None:
        while self._srv is not None:
            try:
                client, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        up = None
        try:
            client.settimeout(30)
            req = b""
            while b"\r\n\r\n" not in req:
                chunk = client.recv(4096)
                if not chunk:
                    return
                req += chunk
            line = req.split(b"\r\n", 1)[0].decode("latin1")
            parts = line.split(" ")
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                client.sendall(b"HTTP/1.1 405 Only CONNECT supported\r\n\r\n")
                return
            host, _, port_s = parts[1].rpartition(":")
            port = int(port_s)
            up = self._open_upstream(host, port)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self._pipe(client, up)
        except Exception as exc:  # noqa: BLE001 供预检报告内部转发失败点
            target = locals().get("host", "未知目标")
            self._last_relay_error = f"CONNECT {target}: {_error_detail(exc)}"
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except OSError:
                pass
        finally:
            for s in (client, up):
                if s:
                    try:
                        s.close()
                    except OSError:
                        pass

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket) -> None:
        try:
            while True:
                r, _, _ = select.select([a, b], [], [], 60)
                if not r:
                    return
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        except OSError:
            pass

    # ---- 连通性分级探测 ----
    def probe(self) -> tuple[str, str]:
        """返回 (状态, 明细)。状态 ∈
        gateway_unreachable / auth_failed / exit_blocked / ok。"""
        # 1) 网关是否可达 + 认证是否通过（连一个稳定公网 IP 1.1.1.1:443 试探出口）
        try:
            s = self._socks5_connect("1.1.1.1", 443, send_as_ip=True, timeout=20)
            s.close()
            return ("ok", "SOCKS5 握手与出口建立成功")
        except VietnamProxyError as exc:
            code = str(exc)
            if "AUTH_FAILED" in code or "NO_AUTH" in code:
                return ("auth_failed", code)
            if "CONNECT_RC" in code or "NO_REPLY" in code:
                # 认证过了但建不了出口连接——通常是流量/余额或会话被限
                return ("exit_blocked", code)
            return ("gateway_unreachable", code)
        except OSError as exc:
            return ("gateway_unreachable", f"{type(exc).__name__}: {exc}")

    def diagnose_target(self, test_url: str, timeout: float) -> tuple[str, list[str]]:
        """逐层验证 NSO 链路，返回最接近根因的状态和完整检查明细。"""
        parsed = urllib.parse.urlsplit(test_url)
        host = parsed.hostname
        port = parsed.port or 443
        report: list[str] = []

        def progress(step: str) -> None:
            print(f"Vietnam 诊断：{step}", flush=True)

        progress("[1/6] 检查 IPRoyal 网关、认证和通用出口")
        proxy_status, proxy_detail = self.probe()
        report.append(f"[1/6] 通用代理出口: {proxy_status}；{proxy_detail}")
        if proxy_status != "ok":
            return proxy_status, report
        if not host:
            report.append(f"[2/6] 目标 URL: 无法取得主机名（{test_url}）")
            return "target_url_invalid", report

        progress(f"[2/6] 解析目标 {host}（DoH、备用 IP、系统 DNS 对照）")
        doh_ip, doh_error = self._doh_query(host)
        selected_ip = self._dns_cache.get(host)
        selected_source = self._dns_source.get(host)
        if not selected_ip:
            selected_ip = doh_ip or GOV_IP_FALLBACK.get(host)
            selected_source = "Cloudflare DoH" if doh_ip else "代码内置备用 IP"
        system_ips: list[str] = []
        system_dns_error = None
        try:
            system_ips = list(dict.fromkeys(
                item[4][0] for item in socket.getaddrinfo(host, port, socket.AF_INET)
            ))
        except OSError as exc:
            system_dns_error = _error_detail(exc)
        report.append(
            f"[2/6] 目标解析: 选用 {selected_ip or '无'}（{selected_source or '无来源'}）；"
            f"DoH={doh_ip or '失败'}"
            + (f"，DoH错误={doh_error}" if doh_error else "")
            + f"；系统DNS={','.join(system_ips) or '失败'}"
            + (f"，系统DNS错误={system_dns_error}" if system_dns_error else "")
        )
        if not selected_ip:
            return "target_dns_failed", report

        candidates = [selected_ip]
        candidates.extend(ip for ip in system_ips if ip not in candidates)
        candidate_timeout = max(5.0, min(20.0, timeout))
        failures: list[tuple[str, str, str]] = []
        for candidate_ip in candidates[:3]:
            progress(f"[3/6] 经 IPRoyal 建立 {candidate_ip}:{port} TCP 隧道")
            try:
                sock = self._socks5_connect(
                    candidate_ip, port, send_as_ip=True, timeout=candidate_timeout)
                sock.close()
                report.append(f"[3/6] NSO TCP 隧道: 成功（{candidate_ip}:{port}）")
            except Exception as exc:  # noqa: BLE001 继续尝试 DNS 对照地址
                detail = _error_detail(exc)
                report.append(f"[3/6] NSO TCP 隧道: 失败（{candidate_ip}:{port}）；{detail}")
                failures.append((candidate_ip, "target_tcp_failed", detail))
                continue

            progress(f"[4/6] 对 {candidate_ip} 执行 TLS 握手（SNI={host}）")
            raw_sock = None
            tls_sock = None
            try:
                raw_sock = self._socks5_connect(
                    candidate_ip, port, send_as_ip=True, timeout=candidate_timeout)
                tls_sock = ssl.create_default_context().wrap_socket(
                    raw_sock, server_hostname=host)
                raw_sock = None
                cipher = tls_sock.cipher()
                report.append(
                    f"[4/6] TLS/SNI: 成功（{tls_sock.version()}，"
                    f"{cipher[0] if cipher else '未知密码套件'}）"
                )
            except Exception as exc:  # noqa: BLE001 继续尝试 DNS 对照地址
                detail = _error_detail(exc)
                report.append(f"[4/6] TLS/SNI: 失败（IP {candidate_ip}）；{detail}")
                failures.append((candidate_ip, "target_tls_failed", detail))
                if raw_sock:
                    raw_sock.close()
                continue

            progress("[5/6] 发送 HTTP 请求并等待 NSO 首包")
            path = parsed.path or "/"
            if parsed.query:
                path += f"?{parsed.query}"
            try:
                tls_sock.settimeout(candidate_timeout)
                tls_sock.sendall(
                    f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                    "User-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n".encode("ascii")
                )
                first_chunk = tls_sock.recv(4096)
                if not first_chunk:
                    raise ConnectionError("TLS 已建立，但服务器未返回 HTTP 数据便关闭连接")
                status_line = first_chunk.split(b"\r\n", 1)[0].decode("latin1", "replace")
                report.append(f"[5/6] NSO HTTP 首包: {status_line}（IP {candidate_ip}）")
                if candidate_ip != selected_ip:
                    report.append(
                        f"[6/6] 结论: 当前选用的 {selected_ip} 不通，但系统 DNS 地址 "
                        f"{candidate_ip} 可用；DoH/内置备用 IP 已过期或被拦截。"
                    )
                    return "target_ip_mismatch", report
                report.append(
                    "[6/6] 结论: 直连 SOCKS→NSO 的 TCP、TLS、HTTP 均正常；"
                    "原失败位于本地 HTTP CONNECT 转发或 urllib 请求层。"
                )
                return "local_relay_or_urllib_failed", report
            except Exception as exc:  # noqa: BLE001 继续尝试 DNS 对照地址
                detail = _error_detail(exc)
                report.append(f"[5/6] NSO HTTP 首包: 失败（IP {candidate_ip}）；{detail}")
                failures.append((candidate_ip, "target_http_failed", detail))
            finally:
                if tls_sock:
                    tls_sock.close()

        if failures:
            depth = {
                "target_tcp_failed": 1,
                "target_tls_failed": 2,
                "target_http_failed": 3,
            }
            _, status, _ = max(failures, key=lambda item: depth[item[1]])
            report.append(
                f"[6/6] 结论: 所有候选 NSO 地址均失败；链路最深到达={status}。"
            )
            return status, report
        report.append("[6/6] 结论: 没有可探测的 NSO IPv4 地址。")
        return "target_dns_failed", report


def preflight_check(relay: VietnamRelay, pf_cfg: dict) -> None:
    """抓数据前验证越南代理可用；失败时按原因给出可读提示并抛错。"""
    test_url = pf_cfg.get("test_url", "https://www.nso.gov.vn/en/")
    timeout = float(pf_cfg.get("timeout_seconds", 40))
    handler = urllib.request.ProxyHandler({"http": relay.proxy_url, "https": relay.proxy_url})
    opener = urllib.request.build_opener(handler)
    request = urllib.request.Request(test_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with opener.open(request, timeout=timeout) as resp:
            resp.read(2048)
        exit_ip = _exit_ip(opener, timeout)
        tail = f"，出口 IP {exit_ip}" if exit_ip else ""
        print(f"Vietnam 预检：越南代理可用{tail}，开始抓取 nso.gov.vn")
        return
    except Exception as first_error:  # noqa: BLE001 目标不通，进一步逐层诊断
        original_error = _error_detail(first_error)
        relay_error = relay._last_relay_error
        status, report = relay.diagnose_target(test_url, timeout)

    hints = {
        "gateway_unreachable": (
            "连不上 IPRoyal 网关（{host}:{port}）。请确认：①网络正常、能出国"
            "（若用 Clash/翻墙，需保持开启）；②config.yaml 里的 upstream_host/port 正确。"
        ),
        "auth_failed": (
            "IPRoyal 认证失败。请检查 config.yaml 里的 username / password 是否正确、是否过期。"
        ),
        "exit_blocked": (
            "已连上 IPRoyal 但无法建立出口连接——很可能是【流量/余额用尽】或该会话被限流。"
            "请登录 IPRoyal 后台查看剩余流量与账户状态；必要时更换套餐或凭据。"
        ),
        "ok": (
            "越南代理通用出口正常，但访问 {url} 的具体阶段失败。"
        ),
        "target_url_invalid": "测试 URL 无效：{url}。",
        "target_dns_failed": "无法解析 {url} 的目标 IPv4 地址。",
        "target_tcp_failed": "代理已认证，但无法建立到 NSO 目标 IP:443 的 TCP 隧道。",
        "target_tls_failed": "已建立到 NSO 的 TCP 隧道，但 TLS/SNI 握手失败。",
        "target_http_failed": "NSO 的 TCP 与 TLS 已建立，但服务器未返回 HTTP 首包。",
        "target_ip_mismatch": (
            "脚本选用的 NSO 地址不可用，但系统 DNS 地址可用；请更新 DoH/备用 IP。"
        ),
        "local_relay_or_urllib_failed": (
            "SOCKS→NSO 全链路正常，失败集中在本地 HTTP CONNECT 转发或 urllib 层。"
        ),
    }
    msg = hints.get(status, "越南代理不可用").format(
        host=relay.host, port=relay.port, url=test_url)
    details = [f"原始请求: {original_error}"]
    if relay_error:
        details.append(f"本地转发: {relay_error}")
    details.extend(report)
    formatted = "\n  ".join(details)
    raise VietnamProxyError(
        f"[越南代理预检失败/{status}] {msg}\n  分层诊断:\n  {formatted}"
    )


def _exit_ip(opener: urllib.request.OpenerDirector, timeout: float) -> str | None:
    for url in ("https://ipv4.icanhazip.com", "https://ipinfo.io/ip"):
        try:
            with opener.open(url, timeout=min(20, timeout)) as resp:
                return resp.read(64).decode().strip()
        except Exception:
            continue
    return None


# ==========================================================================
# 抓取
# ==========================================================================
def clean(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def normalize_commodity(value: object, flow: str) -> str | None:
    return FLOW_LABELS[flow].get(clean(value))


def source_page(year: int) -> str:
    return SOURCE_PAGES.get(
        year,
        f"https://www.nso.gov.vn/en/data-and-statistics/{year}/03/"
        f"exports-and-imports-value-by-months-of-{year}/",
    )


def build_openers(proxy: str | None) -> list[tuple[str, urllib.request.OpenerDirector]]:
    openers = []
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        openers.append((f"代理 {proxy}", urllib.request.build_opener(handler)))
    else:
        openers.append(("系统网络设置", urllib.request.build_opener()))
        openers.append(("直连", urllib.request.build_opener(urllib.request.ProxyHandler({}))))
    return openers


def fetch_bytes(url: str, proxy: str | None = None, wayback: bool = True) -> bytes:
    last_error: Exception | None = None
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 VietnamTextileFetcher/3.0"},
    )
    attempts = []
    for network_name, opener in build_openers(proxy):
        for delay in (0, 2, 5):
            time.sleep(delay)
            try:
                with opener.open(request, timeout=60) as response:
                    return response.read()
            except OSError as exc:
                last_error = exc
        attempts.append(f"{network_name}: {type(last_error).__name__}")
    if wayback:
        try:
            return fetch_wayback(url, proxy)
        except (OSError, RuntimeError, ValueError) as exc:
            attempts.append(f"Web Archive 回退: {type(exc).__name__}")
    raise RuntimeError(
        f"无法访问越南 NSO 数据源 {url}；已尝试 {', '.join(attempts)}。"
        "请检查 config.yaml 越南代理，或用 --proxy 指定其它代理。"
    )


def fetch_wayback(url: str, proxy: str | None) -> bytes:
    """越南代理不可用时的最后回退：改用 web.archive.org 的最新 200 快照。"""
    api = (
        "https://web.archive.org/cdx/search/cdx?url="
        + urllib.parse.quote(url, safe="")
        + "&output=json&filter=statuscode:200&fl=timestamp&limit=-1"
    )
    rows = json.loads(fetch_bytes(api, proxy, wayback=False).decode())
    if len(rows) < 2:
        raise RuntimeError(f"Web Archive 没有该地址的快照: {url}")
    timestamp = rows[-1][0]
    snapshot = f"https://web.archive.org/web/{timestamp}id_/{url}"
    payload = fetch_bytes(snapshot, proxy, wayback=False)
    print(f"Vietnam: 直取失败，使用 Web Archive 快照 {timestamp} <- {url}")
    return payload


def _customs_fetch(url: str, data: bytes | None = None) -> bytes:
    """Fetch the Vietnam Customs public API/files directly with short retries."""
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 VietnamTextileFetcher/3.0",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    last_error: Exception | None = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for delay in (0, 2, 5):
        time.sleep(delay)
        try:
            with opener.open(request, timeout=60) as response:
                return response.read()
        except OSError as exc:
            last_error = exc
    raise RuntimeError(f"越南海关请求失败 {url}: {last_error}")


def discover_customs_monthly_reports(year: int) -> dict[tuple[int, str], str]:
    """Return {(month, flow): public PDF URL} from the Customs page's own API."""
    params = {
        "skip": 0,
        "take": 3000,
        "ky": "",
        "textSearch": "",
        "the_loai": "0",
        "thoigianCongBo": "",
        "typeName": "GetListSoLieu",
        "language": "TIENG_ANH",
    }
    payload = _customs_fetch(CUSTOMS_LIST_URL, json.dumps(params).encode())
    rows = json.loads(payload).get("arr") or []
    pattern = re.compile(
        r"^Statistics of main (imports|exports) by month \(([A-Za-z]+) (\d{4})\)$",
        re.I,
    )
    reports: dict[tuple[int, str], str] = {}
    for row in rows:
        match = pattern.match(str(row.get("TIEU_DE") or "").strip())
        if not match or int(match.group(3)) != year:
            continue
        month = MONTH_NAMES.get(match.group(2).lower())
        flow = "import" if match.group(1).lower() == "imports" else "export"
        if not month:
            continue
        # Prefer a revised public file, then preliminary. The "final" field is often
        # a future-dated private 10.x address and therefore is deliberately last.
        candidates = (
            row.get("FILE_DIEU_CHINH"), row.get("FILE_SO_BO"), row.get("FILE_CHINH_THUC")
        )
        url = next(
            (
                str(value) for value in candidates
                if value and str(value).lower() != "null"
                and str(value).startswith("https://files.customs.gov.vn/")
            ),
            None,
        )
        if url:
            reports[month, flow] = url
    return reports


def parse_customs_monthly_pdf(payload: bytes, flow: str) -> float:
    """Sum textile reporting-month values from a Customs table 14B/15B PDF."""
    from pypdf import PdfReader

    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(payload)).pages
    )
    values: list[float] = []
    for label in CUSTOMS_MONTHLY_LABELS[flow]:
        # A USD-only row is "<label>USD <value> ..."; a quantity row is
        # "<label>Ton <volume> <value> ...". In both cases the captured number is
        # the first reporting-month value in USD, not the year-to-date value.
        match = re.search(
            re.escape(label) + r"(?:USD\s+|Ton\s+[\d,]+\s+)([\d,]+)",
            pdf_text,
            re.I,
        )
        if not match:
            raise RuntimeError(f"越南海关 PDF 缺少纺织分类: {flow} / {label}")
        values.append(float(match.group(1).replace(",", "")))
    return sum(values)


def fetch_customs_fallback_year(year: int) -> list[dict[str, object]]:
    """Build wide monthly totals for months having both Customs import/export PDFs."""
    reports = discover_customs_monthly_reports(year)
    rows: list[dict[str, object]] = []
    for month in range(1, 13):
        if not all((month, flow) in reports for flow in ("import", "export")):
            continue
        try:
            values = {
                flow: parse_customs_monthly_pdf(
                    _customs_fetch(reports[month, flow]), flow
                )
                for flow in ("import", "export")
            }
        except Exception as exc:
            print(
                f"Vietnam Customs fallback: WARNING {year}-{month:02d} "
                f"月报分类不完整，跳过该月: {exc}"
            )
            continue
        rows.append({
            "年月": f"{year:04d}{month:02d}",
            "年": year,
            "月": month,
            "进口额": values["import"],
            "出口额": values["export"],
        })
        print(f"Vietnam Customs fallback: {year}-{month:02d} 进口/出口月报解析成功")
    if not rows:
        raise RuntimeError(f"越南海关未找到 {year} 年成对的进口/出口月报")
    return rows


def flow_from_filename(filename: str) -> str | None:
    normalized = filename.lower()
    if "import" in normalized or re.match(r"e0?2(?:\D|$)", normalized):
        return "import"
    if "export" in normalized or re.match(r"e0?1(?:\D|$)", normalized):
        return "export"
    return None


def discover_all_xls(year: int, proxy: str | None) -> list[str]:
    """返回该年份源页面上所有 .xls 附件的完整 URL（用于归档 manifest 全量）。"""
    page = source_page(year)
    html = fetch_bytes(page, proxy).decode("utf-8", "ignore")
    urls = []
    for href in re.findall(r'''href=["']([^"']+\.xls(?:\?[^"']*)?)["']''', html, re.I):
        urls.append(urllib.parse.urljoin(page, href))
    seen, ordered = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered


def discover_sources(year: int, proxy: str | None) -> dict[str, str]:
    candidates: dict[str, list[str]] = defaultdict(list)
    for url in discover_all_xls(year, proxy):
        filename = Path(urllib.parse.urlparse(url).path).name
        flow = flow_from_filename(filename)
        if flow and str(year) in filename:
            candidates[flow].append(url)
    return {flow: max(urls, key=source_revision) for flow, urls in candidates.items()}


def source_revision(url: str) -> tuple[int, str]:
    filename = Path(urllib.parse.urlparse(url).path).stem
    match = re.search(r"-(\d+)$", filename)
    return (int(match.group(1)) if match else 0, filename)


def known_sources(year: int) -> dict[str, str]:
    filenames = KNOWN_FILES.get(year)
    if not filenames:
        return {}
    page = source_page(year)
    upload_match = re.search(r"/(20\d{2})/(\d{2})/", page)
    if not upload_match:
        return {}
    upload_year, upload_month = upload_match.groups()
    base = f"https://www.nso.gov.vn/wp-content/uploads/{upload_year}/{upload_month}/"
    return {"export": base + filenames[0], "import": base + filenames[1]}


def resolve_sources(year: int, offline: bool, proxy: str | None) -> dict[str, str]:
    fallback = known_sources(year)
    if offline:
        if not fallback:
            raise RuntimeError(f"离线模式没有 {year} 年的内置来源")
        return fallback
    if year == date.today().year or not fallback:
        try:
            discovered = discover_sources(year, proxy)
            if set(discovered) == {"export", "import"}:
                return discovered
        except RuntimeError as exc:
            if not fallback:
                raise
            print(f"Vietnam: 当前年份附件发现失败，使用已知地址 ({exc})")
    return fallback


def cached_workbook(
    year: int, flow: str, url: str, offline: bool, proxy: str | None,
    refresh_all: bool = False,
) -> Path:
    filename = Path(urllib.parse.urlparse(url).path).name
    path = CACHE_DIR / str(year) / filename
    refresh = (refresh_all or year == date.today().year) and not offline
    if refresh or not path.exists():
        if offline:
            raise RuntimeError(f"离线缓存不存在: {path}")
        try:
            payload = fetch_bytes(url, proxy)
        except RuntimeError:
            if not path.exists():
                raise
            print(f"Vietnam: 刷新 {year} {flow} 失败，沿用本地缓存 {path.name}")
            verify_workbook(path)
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        verify_workbook(temporary)
        temporary.replace(path)
        print(f"Vietnam: 下载 {year} {flow} -> {path.name}")
    verify_workbook(path)
    return path


def archive_year_sources(year: int, proxy: str | None) -> int:
    """把该年份页面上所有 .xls 附件下载到 cache/vietnam/<year>/（已存在则跳过）。

    用于完整镜像 sources_manifest 里的全部文件及网站新进文件；返回新下载数量。
    """
    try:
        urls = discover_all_xls(year, proxy)
    except RuntimeError as exc:
        print(f"Vietnam: {year} 年附件列表获取失败，跳过归档 ({exc})")
        return 0
    new = 0
    for url in urls:
        filename = Path(urllib.parse.urlparse(url).path).name
        path = CACHE_DIR / str(year) / filename
        if path.exists():
            continue
        try:
            payload = fetch_bytes(url, proxy, wayback=False)
        except RuntimeError as exc:
            print(f"Vietnam: 归档下载失败 {filename} ({exc})")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        new += 1
        print(f"Vietnam: 归档 {year} -> {filename} ({len(payload)} B)")
    return new


def verify_workbook(path: Path) -> None:
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError("缺少依赖 xlrd，请运行: python -m pip install xlrd==2.0.1") from exc
    try:
        book = xlrd.open_workbook(path, on_demand=True)
        book.release_resources()
    except Exception as exc:
        raise RuntimeError(f"下载内容不是可读取的 Excel 文件: {path}") from exc


def month_columns(sheet: object) -> list[tuple[int, int]]:
    for header_row in range(min(8, sheet.nrows - 1)):
        current_month = None
        found: list[tuple[int, int]] = []
        for column in range(sheet.ncols):
            header = clean(sheet.cell_value(header_row, column))
            first_word = header.split(" ", 1)[0] if header else ""
            if first_word in MONTH_NAMES:
                current_month = MONTH_NAMES[first_word]
            subheader = clean(sheet.cell_value(header_row + 1, column))
            if current_month and "value" in subheader:
                found.append((column, current_month))
        if found:
            return found
    return []


def number(value: object) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def parse_workbook(path: Path, page: str, url: str, year: int, flow: str) -> list[dict[str, object]]:
    import xlrd

    multiplier = 1_000_000 if year in (2016, 2017) else 1_000
    source_unit = "million USD" if multiplier == 1_000_000 else "1000 USD"
    records: list[dict[str, object]] = []
    book = xlrd.open_workbook(path)
    for sheet in book.sheets():
        value_columns = month_columns(sheet)
        if not value_columns:
            continue
        for row in range(sheet.nrows):
            for label_column in range(min(6, sheet.ncols)):
                original = str(sheet.cell_value(row, label_column)).strip()
                normalized = normalize_commodity(original, flow)
                if not normalized:
                    continue
                for column, month in value_columns:
                    source_value = number(sheet.cell_value(row, column))
                    if source_value is not None and source_value >= 0:
                        records.append({
                            "source_page": page,
                            "source_file": url,
                            "year": year,
                            "month": month,
                            "flow": flow,
                            "commodity_original": original,
                            "commodity_normalized": normalized,
                            "value": source_value * multiplier,
                            "source_value": source_value,
                            "source_unit": source_unit,
                        })
                break
    if not records:
        raise RuntimeError(f"未能从工作簿解析纺织品数据，可能是官方表格结构发生变化: {path}")
    return records


def aggregate_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    commodity_values: dict[tuple[int, int, str, str], float] = {}
    for row in records:
        key = (
            int(row["year"]), int(row["month"]), str(row["flow"]),
            str(row["commodity_normalized"]),
        )
        value = float(row["value"])
        commodity_values[key] = min(value, commodity_values.get(key, value))

    totals: dict[tuple[int, int, str], float] = defaultdict(float)
    for (year, month, flow, _), value in commodity_values.items():
        totals[year, month, flow] += value

    months = sorted({(year, month) for year, month, _ in totals})
    return [
        {
            "年月": f"{year:04d}{month:02d}",
            "年": year,
            "月": month,
            "进口额": totals[year, month, "import"],
            "出口额": totals[year, month, "export"],
        }
        for year, month in months
    ]


def validate_records(records: list[dict[str, object]]) -> dict[str, object]:
    if not records:
        raise RuntimeError("越南数据为空")
    dates = [str(row["年月"]).replace("-", "").zfill(6) for row in records]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise RuntimeError("越南数据月份乱序或重复")

    expected = []
    year, month = int(dates[0][:4]), int(dates[0][4:])
    end_year, end_month = int(dates[-1][:4]), int(dates[-1][4:])
    while (year, month) <= (end_year, end_month):
        expected.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    missing = sorted(set(expected) - set(dates))
    if missing:
        raise RuntimeError(f"越南数据缺少月份: {', '.join(missing)}")

    for row in records:
        for column in ("进口额", "出口额"):
            value = float(row[column])
            if value <= 0:
                raise RuntimeError(f"越南数据包含无效值: {row['年月']} {column}={value}")

    latest_year, latest_month = int(dates[-1][:4]), int(dates[-1][4:])
    age = (date.today().year - latest_year) * 12 + date.today().month - latest_month
    if age > 6:
        raise RuntimeError(f"越南数据过旧，最新月份为 {dates[-1]}")
    return {"detail_rows": None, "months": len(records), "coverage": [dates[0], dates[-1]]}


def write_csv(path: Path, rows: list[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


# ==========================================================================
# 主流程
# ==========================================================================
def main(
    output_path: Path = DEFAULT_OUTPUT,
    offline: bool = False,
    proxy: str | None = None,
    config_path: Path = DEFAULT_CONFIG,
    preflight: bool = True,
    archive_all: bool = False,
    refresh: str = "auto",
    existing_path: Path | None = None,
) -> list[dict[str, object]]:
    # refresh: auto=用 cache/ 且当年刷新; all=忽略缓存全量重抓;
    #          data=existing_path 指向的既有 CSV 当缓存,已整年覆盖的年份不抓,只补缺
    existing: dict[str, dict[str, object]] = {}
    # Always load the published CSV as a safety baseline. In refresh=all it is not
    # used as a download cache, but it fills years whose historical attachments are
    # temporarily unavailable so successful years can still be published.
    if existing_path and existing_path.exists():
        with existing_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if "年月" in row:
                    period = str(row["年月"]).replace("-", "").zfill(6)
                    existing[period] = {
                        "年月": period,
                        "年": int(row["年"]),
                        "月": int(row["月"]),
                        "进口额": float(row["进口额"]),
                        "出口额": float(row["出口额"]),
                    }
                else:
                    period = row["date"].replace("-", "")
                    existing[period] = {
                        "年月": period,
                        "年": int(period[:4]),
                        "月": int(period[4:]),
                        "进口额": float(row["vietnam_import"]),
                        "出口额": float(row["vietnam_export"]),
                    }
        mode = "data 模式缓存" if refresh == "data" else "失败回退基线"
        print(f"Vietnam: 加载 {mode} {existing_path} 已有 {len(existing)} 个月")
    this_year = date.today().year
    relay: VietnamRelay | None = None
    # 未显式给 --proxy、且非离线时，用 config.yaml 启动内嵌越南转发
    if proxy is None and not offline:
        cfg = load_config(config_path)
        vp = cfg.get("vietnam_proxy", {})
        if vp.get("enabled", True):
            relay = VietnamRelay(vp).start()
            proxy = relay.proxy_url
            if preflight and cfg.get("preflight", {}).get("enabled", True):
                preflight_check(relay, cfg.get("preflight", {}))
    skipped_years = 0
    try:
        records: list[dict[str, object]] = []
        customs_fallback_rows: list[dict[str, object]] = []
        failed_years: dict[int, str] = {}
        for year in range(2015, this_year + 1):
            # data 模式:过去年 12 个月齐全则整年跳过,不下载
            if (refresh == "data" and year < this_year
                    and all(f"{year:04d}{month:02d}" in existing for month in range(1, 13))):
                skipped_years += 1
                continue
            year_records: list[dict[str, object]] = []
            try:
                if archive_all and not offline:
                    archive_year_sources(year, proxy)
                sources = resolve_sources(year, offline, proxy)
                if set(sources) != {"export", "import"}:
                    raise RuntimeError(f"{year} 年缺少出口或进口官方附件")
                for flow, url in sources.items():
                    path = cached_workbook(year, flow, url, offline, proxy,
                                           refresh_all=refresh == "all")
                    year_records.extend(
                        parse_workbook(path, source_page(year), url, year, flow)
                    )
            except Exception as exc:  # isolate unavailable historical attachments by year
                nso_error = f"{type(exc).__name__}: {exc}"
                try:
                    customs_rows = fetch_customs_fallback_year(year)
                except Exception as customs_exc:
                    failed_years[year] = (
                        f"NSO/GSO={nso_error}; Vietnam Customs="
                        f"{type(customs_exc).__name__}: {customs_exc}"
                    )
                    fallback_months = sum(key.startswith(str(year)) for key in existing)
                    print(
                        f"Vietnam: WARNING {year} 年两个官方来源均失败，"
                        f"沿用现有 {fallback_months} 个月并继续后续年份: "
                        f"{failed_years[year]}"
                    )
                else:
                    customs_fallback_rows.extend(customs_rows)
                    print(
                        f"Vietnam: NSO/GSO {year} 年抓取失败，改用越南海关月报 "
                        f"{len(customs_rows)} 个月: {nso_error}"
                    )
                continue
            records.extend(year_records)
    finally:
        if relay is not None:
            relay.stop()

    unique = {tuple(str(row[column]) for column in DETAIL_COLUMNS): row for row in records}
    details = sorted(
        unique.values(),
        key=lambda row: (row["year"], row["month"], row["flow"], row["commodity_normalized"], row["value"]),
    )
    totals = aggregate_records(details)
    if customs_fallback_rows:
        merged_fresh = {str(row["年月"]): row for row in totals}
        merged_fresh.update({str(row["年月"]): row for row in customs_fallback_rows})
        totals = [merged_fresh[key] for key in sorted(merged_fresh)]
    if existing:
        # Existing rows fill skipped/failed periods; freshly parsed months win.
        merged = {str(row["年月"]): row for row in totals}
        for key, row in existing.items():
            merged.setdefault(key, row)
        totals = [merged[key] for key in sorted(merged)]
    summary = validate_records(totals)
    summary["detail_rows"] = len(details)

    write_csv(output_path, totals, OUTPUT_COLUMNS)
    if skipped_years:
        # 明细只含本次新抓年份,不用它覆盖 cache 里的完整明细存档
        print(f"Vietnam: data 模式跳过 {skipped_years} 个整年,明细缓存保持原样不重写")
    else:
        write_csv(CACHE_DIR / "vietnam_textile_trade_monthly.csv", details, DETAIL_COLUMNS)
    (CACHE_DIR / "validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Vietnam: 写入 {output_path} "
        f"({len(totals)} 个月, {totals[0]['年月']} .. {totals[-1]['年月']})"
    )
    if failed_years:
        print(
            "Vietnam: 部分年份抓取失败但已发布可用结果: "
            + "; ".join(f"{year}: {error}" for year, error in failed_years.items())
        )
    return totals


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="越南代理配置文件，默认同目录 config.yaml")
    parser.add_argument("--offline", action="store_true", help="只使用 cache/vietnam 中的工作簿")
    parser.add_argument("--proxy", help="显式指定 HTTP/HTTPS 代理，覆盖 config.yaml 的内嵌越南转发")
    parser.add_argument("--no-preflight", action="store_true", help="跳过越南代理连通性预检")
    parser.add_argument("--archive-all", action="store_true",
                        help="额外镜像每年页面上全部 .xls（E01-E07），完整存档 manifest；"
                             "默认只抓解析所需的 E01/E02")
    args = parser.parse_args()
    try:
        main(
            args.output,
            offline=args.offline,
            proxy=args.proxy,
            config_path=args.config,
            preflight=not args.no_preflight,
            archive_all=args.archive_all,
        )
    except VietnamProxyError as exc:
        print(f"\n[错误] {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    cli()
