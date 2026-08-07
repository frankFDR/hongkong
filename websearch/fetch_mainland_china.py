# -*- coding: utf-8 -*-
"""Automatically collect official China Customs monthly USD chapter reports."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import pandas as pd
from bs4 import BeautifulSoup

# 走 HTTP 入口：海关 CDN 的 HTTPS 回源链路长期 504（X-JSL-Shadow-Connection: fail），
# 而 HTTP 的知道创宇 412 Challenge 由真实 Chrome 自然执行后可进入业务页。
NAV_URL = (
    "http://gdfs.customs.gov.cn/customs/302249/zfxxgk/2799825/302274/302277/6348926/index.html"
)
HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache" / "mainland_china"
REPORTS_DIR = CACHE_DIR / "reports"
PAGES_DIR = CACHE_DIR / "pages"
FAILURES_DIR = CACHE_DIR / "failures"
PROFILE_DIR = CACHE_DIR / "browser-profile"
STORAGE_STATE_PATH = CACHE_DIR / "storage-state.json"
MANIFEST_PATH = CACHE_DIR / "manifest.json"
FETCH_REPORT_PATH = HERE / "output" / "mainland_china_fetch_report.json"
DEFAULT_IMPORT = HERE / "data" / "mainlandChina_Import.csv"
DEFAULT_EXPORT = HERE / "data" / "mainlandChina_Export.csv"
DEFAULT_OUTPUT = HERE / "data" / "mainlandChina.csv"
LEGACY_BASELINE_DIR = HERE / "backup" / "2026-07-14"
LEGACY_IMPORT = LEGACY_BASELINE_DIR / "mainlandChina_Import.csv"
LEGACY_EXPORT = LEGACY_BASELINE_DIR / "mainlandChina_Export.csv"
CHAPTERS = tuple(range(50, 64))

FALLBACK_OUTPUT_COLUMNS = [
    "年", "月", "第50章 蚕丝", "第51章 羊毛、动物细毛或粗毛;马毛纱线及其机织物",
    "第52章 棉花", "第53章 其他植物纺织纤维;纸纱线及其机织物",
    "第54章 化学纤维长丝;化学纤维纺织材料制扁条及类似品", "第55章 化学纤维短纤",
    "第56章 絮胎、毡呢及无纺织物;特种纱线;线、绳、索、缆及其制品",
    "第57章 地毯及纺织材料的其他铺地制品",
    "第58章 特种机织物;簇绒织物;花边;装饰毯;装饰带;刺绣品",
    "第58章 浸渍、涂布、包覆或层压的纺织物;工业用纺织制品",
    "第60章 针织物及钩编织物", "第61章 针织或钩编的服装及衣着附件",
    "第62章 非针织或非钩编的服装及衣着附件",
    "第63章 其他纺织制成品;成套物品;旧衣着及旧纺织品;碎织物", "总计",
]


def _legacy_columns() -> list[str]:
    candidates = [DEFAULT_IMPORT, *sorted((HERE / "backup").glob("*/mainlandChina_Import.csv"), reverse=True)]
    for candidate in candidates:
        if candidate.exists():
            with candidate.open(encoding="utf-8-sig", newline="") as handle:
                header = next(csv.reader(handle), [])
            if len(header) == 17:
                return header
    return FALLBACK_OUTPUT_COLUMNS


OUTPUT_COLUMNS = _legacy_columns()


class FetchError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# ==========================================================================
# 仅海关流量绕过 Clash TUN 的直连层
# --------------------------------------------------------------------------
# 根因：当 Clash 处于 TUN 全局模式时，它在网络层劫持所有流量到海外节点，导致
# 海关 CDN（知道创宇/365cyd）对境外访客拒绝回源，返回 504 / Challenge 回跳 400。
# 解决：① 用公共 DoH 解析海关域名的真实国内 CDN IP；② 给这些 IP 加 /32 路由
# 指向物理网关（仅这几个 IP，Clash 处理其它所有流量不受影响）；③ Chrome 用
# --no-proxy-server + --host-resolver-rules 强制这些域名直连到真实 IP。
# ==========================================================================
CUSTOMS_DOMAINS = ("gdfs.customs.gov.cn", "www.customs.gov.cn", "stats.customs.gov.cn")
DOH_ENDPOINTS = (
    "https://223.5.5.5/resolve",      # AliDNS
    "https://120.53.53.53/resolve",   # DNSPod
    "https://1.12.12.12/resolve",     # DNSPod 备
)
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_DIRECT_BYPASS_CACHE: dict[str | None, tuple[str, dict[str, list[str]], str]] = {}


def _doh_resolve(domain: str, timeout: float = 12.0) -> list[str]:
    """通过公共 DoH 解析 domain 的国内 CDN A 记录（不经系统代理，避免依赖 Clash）。"""
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
    )
    for endpoint in DOH_ENDPOINTS:
        try:
            request = urllib.request.Request(
                f"{endpoint}?name={domain}&type=A",
                headers={"accept": "application/dns-json"},
            )
            with opener.open(request, timeout=timeout) as response:
                answers = json.load(response).get("Answer", [])
            ips = [a["data"] for a in answers
                   if a.get("type") == 1 and _IPV4_RE.match(str(a.get("data", "")))]
            if ips:
                return list(dict.fromkeys(ips))
        except Exception:
            continue
    return []


IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
# Clash TUN 常见网段 198.18.*；Tailscale 100.*（CGNAT 段）。虚拟网卡名前缀同理排除。
_VIRTUAL_GATEWAY_PREFIXES = ("198.18.", "100.")
_VIRTUAL_IFACE_PREFIXES = ("utun", "tun", "tap", "wg", "tailscale", "ipsec", "ppp", "clash", "meta")


def _is_physical_next_hop(candidate: str) -> bool:
    return bool(_IPV4_RE.match(candidate)) and not candidate.startswith(_VIRTUAL_GATEWAY_PREFIXES)


def _detect_gateway_windows() -> str | None:
    script = (
        "Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | "
        "Where-Object { $_.NextHop -ne '0.0.0.0' -and $_.NextHop -notlike '198.18.*' "
        "-and $_.NextHop -notlike '100.*' } | Sort-Object RouteMetric | "
        "Select-Object -First 1 -ExpandProperty NextHop"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=25,
    )
    for line in result.stdout.splitlines():
        candidate = line.strip()
        if _IPV4_RE.match(candidate):
            return candidate
    return None


def _detect_gateway_macos() -> str | None:
    # Clash TUN 在 macOS 上通常不改 default，而是加 1/1 + 128.0/1 两条更长前缀；
    # 但也有工具直接把 default 指到 utun。因此逐条看 default 行，跳过虚拟网卡/网段。
    output = subprocess.run(
        ["netstat", "-rn", "-f", "inet"], capture_output=True, text=True, timeout=25,
    ).stdout
    for line in output.splitlines():
        columns = line.split()
        if len(columns) >= 4 and columns[0] == "default":
            gateway, netif = columns[1], columns[3]
            if _is_physical_next_hop(gateway) and not netif.lower().startswith(_VIRTUAL_IFACE_PREFIXES):
                return gateway
    return None


def _detect_gateway_linux() -> str | None:
    output = subprocess.run(
        ["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=25,
    ).stdout
    best: tuple[int, str] | None = None
    for line in output.splitlines():
        columns = line.split()
        gateway = columns[columns.index("via") + 1] if "via" in columns else ""
        device = columns[columns.index("dev") + 1] if "dev" in columns else ""
        metric = int(columns[columns.index("metric") + 1]) if "metric" in columns else 0
        if _is_physical_next_hop(gateway) and not device.lower().startswith(_VIRTUAL_IFACE_PREFIXES):
            if best is None or metric < best[0]:
                best = (metric, gateway)
    return best[1] if best else None


def _detect_physical_gateway(override: str | None = None) -> str:
    """返回物理默认网关（排除 Clash TUN 198.18.* 与 Tailscale 100.* 等虚拟出口）。"""
    if override:
        return override
    try:
        if IS_WINDOWS:
            candidate = _detect_gateway_windows()
        elif IS_MACOS:
            candidate = _detect_gateway_macos()
        else:
            candidate = _detect_gateway_linux()
        if candidate:
            return candidate
    except Exception:
        pass
    raise FetchError("no_gateway", "无法自动检测物理网关，请用 --gateway 显式指定")


def _network_24(ip: str) -> str:
    return ".".join(ip.split(".")[:3]) + ".0"


def _mask_prefix(mask: str) -> int:
    return sum(bin(int(octet)).count("1") for octet in mask.split("."))


def _route_exists(network: str, mask: str, gateway: str) -> bool:
    prefix = _mask_prefix(mask)
    try:
        if IS_WINDOWS:
            output = subprocess.run(
                ["route", "print", "-4", network], capture_output=True, text=True, timeout=15,
            ).stdout
            for line in output.splitlines():
                columns = line.split()
                if (len(columns) >= 3 and columns[0] == network
                        and columns[1] == mask and columns[2] == gateway):
                    return True
            return False
        if IS_MACOS:
            # route get 会返回「最长匹配」的路由（可能是 Clash 的 128.0/1），
            # 因此必须核对 destination 恰好是该网段；macOS 会省略尾部 .0（如 223.99.255）。
            output = subprocess.run(
                ["route", "-n", "get", "-net", f"{network}/{prefix}"],
                capture_output=True, text=True, timeout=15,
            ).stdout
            fields: dict[str, str] = {}
            for line in output.splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    fields[key.strip()] = value.strip()
            destination = fields.get("destination", "")
            while destination.count(".") < 3:
                destination += ".0"
            return destination == network and fields.get("gateway") == gateway
        output = subprocess.run(
            ["ip", "-4", "route", "show", f"{network}/{prefix}"],
            capture_output=True, text=True, timeout=15,
        ).stdout
        for line in output.splitlines():
            columns = line.split()
            if "via" in columns and columns[columns.index("via") + 1] == gateway:
                return True
        return False
    except Exception:
        return False


def _run_privileged(commands: list[str]) -> None:
    """以管理员权限执行一批路由命令：Windows 走 UAC，macOS 走系统授权弹窗，Linux 走 sudo。"""
    script = " ; ".join(commands)
    if not IS_WINDOWS and hasattr(os, "geteuid") and os.geteuid() == 0:
        result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=180)
    elif IS_MACOS:
        osa = (
            f'do shell script "{script}" '
            'with prompt "DataFetch 需要修改海关 CDN 直连路由" with administrator privileges'
        )
        result = subprocess.run(
            ["osascript", "-e", osa], capture_output=True, text=True, timeout=180,
        )
    else:
        result = subprocess.run(
            ["sudo", "-n", "/bin/sh", "-c", script], capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0 and sys.stdin.isatty():
            result = subprocess.run(["sudo", "/bin/sh", "-c", script], timeout=180)
    if result.returncode != 0:
        raise FetchError(
            "route_failed",
            "修改海关直连路由需要管理员授权，但授权未通过（或被拒绝）。"
            "请重试并在系统弹窗/sudo 提示中完成授权，或用管理员身份运行本脚本。",
        )


def _apply_routes(networks: list[str], mask: str, gateway: str, action: str) -> list[str]:
    """对缺失(add)/存在(delete)的路由做一次带管理员授权的批量操作，返回实际处理的网段。

    海关 CDN（知道创宇）会在几个 /24 段内轮换 IP，因此按 /24 加路由，避免每次
    IP 变动都重新弹授权。这些网段只属于海关 CDN，Clash 处理其它流量不受影响。
    """
    prefix = _mask_prefix(mask)
    if action == "add":
        targets = [net for net in networks if not _route_exists(net, mask, gateway)]
    else:
        targets = [net for net in networks if _route_exists(net, mask, gateway)]
    if not targets:
        return []
    if IS_WINDOWS:
        if action == "add":
            make = lambda net: f"route add {net} mask {mask} {gateway} metric 1"
        else:
            make = lambda net: f"route delete {net}"
        batch = " & ".join(make(net) for net in targets)
        powershell = f"$p = Start-Process cmd -Verb RunAs -Wait -PassThru -ArgumentList '/c','{batch}'; exit $p.ExitCode"
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", powershell],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            raise FetchError(
                "route_failed",
                "添加海关直连路由需要管理员授权，但 UAC 未通过（或被拒绝）。"
                "请重试并在弹窗中选择“是”，或用管理员身份运行本脚本。",
            )
        return targets
    if IS_MACOS:
        if action == "add":
            commands = [f"route -n add -net {net}/{prefix} {gateway}" for net in targets]
        else:
            commands = [f"route -n delete -net {net}/{prefix}" for net in targets]
    else:
        if action == "add":
            commands = [f"ip route add {net}/{prefix} via {gateway}" for net in targets]
        else:
            commands = [f"ip route del {net}/{prefix}" for net in targets]
    _run_privileged(commands)
    return targets


def _resolve_customs_ips() -> dict[str, list[str]]:
    resolved: dict[str, list[str]] = {}
    for domain in CUSTOMS_DOMAINS:
        pool: list[str] = []
        for _ in range(3):  # CDN 轮换，多查几次以覆盖更多节点
            pool.extend(_doh_resolve(domain))
        ips = list(dict.fromkeys(pool))
        if ips:
            resolved[domain] = ips
    return resolved


def prepare_direct_bypass(gateway_override: str | None = None) -> tuple[str, dict[str, list[str]], str]:
    """解析海关域名真实 IP、按 /24 加直连路由，返回 (host-resolver-rules, 解析表, 网关)。

    只给「实际用作出口」的 IP（每域名一枚）加 /24 路由，且优先选落在已有直连路由段内
    的候选 IP：海关 CDN（知道创宇）会在多个运营商 /24 间轮换，这样能把新增路由(=UAC 弹窗)
    降到最少，长时间全量抓取时不会频繁被授权打断。
    """
    if gateway_override in _DIRECT_BYPASS_CACHE:
        return _DIRECT_BYPASS_CACHE[gateway_override]

    resolved = _resolve_customs_ips()
    if "gdfs.customs.gov.cn" not in resolved:
        raise FetchError("resolve_failed", "无法解析 gdfs.customs.gov.cn 的真实国内 IP（DoH 全部失败）")
    gateway = _detect_physical_gateway(gateway_override)

    def routed(ip: str) -> bool:
        return _route_exists(_network_24(ip), "255.255.255.0", gateway)

    # 只有 gdfs 承载全部月报页面与 .xls，是唯一必须直连的域名，优先选已在直连段内的
    # 出口 IP，实在没有才新增一条 /24（一次 UAC）。
    gdfs_ips = resolved["gdfs.customs.gov.cn"]
    gdfs_ip = next((ip for ip in gdfs_ips if routed(ip)), gdfs_ips[0])
    chosen: dict[str, str] = {"gdfs.customs.gov.cn": gdfs_ip}
    if not routed(gdfs_ip):
        _apply_routes([_network_24(gdfs_ip)], "255.255.255.0", gateway, "add")
        if not routed(gdfs_ip):
            raise FetchError("route_failed", f"gdfs 直连路由未能建立: {_network_24(gdfs_ip)}（/24）")
        print(f"Mainland China: 已添加海关 CDN 直连路由 {_network_24(gdfs_ip)} (/24) -> 网关 {gateway}", flush=True)
    # www/stats 不参与取数，仅当其 IP 恰好落在已有直连段时顺带映射，绝不为它们新增路由。
    for domain in ("www.customs.gov.cn", "stats.customs.gov.cn"):
        for ip in resolved.get(domain, []):
            if routed(ip):
                chosen[domain] = ip
                break
    rules = ",".join(f"MAP {domain} {ip}" for domain, ip in chosen.items())
    result = rules, {domain: [ip] for domain, ip in chosen.items()}, gateway
    _DIRECT_BYPASS_CACHE[gateway_override] = result
    return result


def prepare_english_direct(gateway_override: str | None = None) -> str:
    """为英文站 english.customs.gov.cn 准备 TUN 直连，返回真实国内 IP。

    英文站虽无 JS 风控，但同样托管在海关 CDN 上：Clash TUN 劫持后经境外出口
    访问会 502。做法与 gdfs 相同——DoH 解析真实 IP、优先复用已有 /24 直连路由，
    没有才新增一条（一次 UAC）；调用方再用「IP 直连 + Host 头」的方式请求。
    """
    domain = "english.customs.gov.cn"
    pool: list[str] = []
    for _ in range(3):
        pool.extend(_doh_resolve(domain))
    ips = list(dict.fromkeys(pool))
    if not ips:
        raise FetchError("resolve_failed", f"无法解析 {domain} 的真实国内 IP（DoH 全部失败）")
    gateway = _detect_physical_gateway(gateway_override)

    def routed(ip: str) -> bool:
        return _route_exists(_network_24(ip), "255.255.255.0", gateway)

    chosen = next((ip for ip in ips if routed(ip)), ips[0])
    if not routed(chosen):
        _apply_routes([_network_24(chosen)], "255.255.255.0", gateway, "add")
        if not routed(chosen):
            raise FetchError("route_failed", f"英文站直连路由未能建立: {_network_24(chosen)}（/24）")
        print(f"Mainland China: 已添加英文站直连路由 {_network_24(chosen)} (/24) -> 网关 {gateway}", flush=True)
    return chosen


def cleanup_direct_bypass(gateway_override: str | None = None) -> None:
    """删除本工具添加的海关 /24 直连路由（重启也会自动清除）。"""
    resolved = _resolve_customs_ips()
    resolved["english.customs.gov.cn"] = list(dict.fromkeys(
        ip for _ in range(3) for ip in _doh_resolve("english.customs.gov.cn")
    ))
    gateway = _detect_physical_gateway(gateway_override)
    networks = sorted({_network_24(ip) for ips in resolved.values() for ip in ips})
    removed = _apply_routes(networks, "255.255.255.0", gateway, "delete")
    print(f"Mainland China: 已清除海关直连路由 {removed or '（无）'}", flush=True)


def force_http_customs(url: str) -> str:
    """把 customs.gov.cn 的链接统一改成 HTTP（HTTPS 源站 504，HTTP+Challenge 可用）。"""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme == "https" and host.endswith("customs.gov.cn"):
        return "http://" + url[len("https://"):]
    return url


@dataclass
class PageResult:
    url: str
    html: str
    state: str
    status: int | None = None
    responses: list[dict[str, Any]] = field(default_factory=list)


def normalize(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", "", str(value)).replace("（", "(").replace("）", ")").upper()


def parse_number(value: object) -> Decimal | None:
    text = normalize(value).replace(",", "").replace("，", "")
    if not text or text in {"-", "—", "--"}:
        return Decimal(0)
    text = text.replace("−", "-")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if number < 0:
        raise ValueError(f"海关月报出现负值: {value}")
    return number


def chapter_from_row(row: pd.Series) -> int | None:
    # 只认「(第)NN章…」这种以章号开头的标签单元格；官方类章表的金额列里也含
    # 50-63 的数字子串（如出口额 76862 含“62”），必须锚定行首并要求紧跟“章”，
    # 否则会把普通商品/金额行误判成 HS 章节。
    for value in row.iloc[:5]:
        text = normalize(value)
        match = re.match(r"(?:第)?(5[0-9]|6[0-3])章", text)
        if match:
            return int(match.group(1))
    return None


def unit_multiplier(text: str) -> int:
    normalized = normalize(text)
    if any(word in normalized for word in ("人民币", "RMB", "元人民币", "CNY")):
        raise ValueError("月报不是美元口径")
    if "万美元" in normalized:
        return 10_000
    if "千美元" in normalized:
        return 1_000
    if "美元" in normalized or "USD" in normalized or "US$" in normalized:
        return 1
    raise ValueError("无法确认月报金额单位为美元")


def column_labels(frame: pd.DataFrame, first_data_row: int) -> list[str]:
    # 从表格顶部一直扫到首个数据行：官方类章网页表的“出口/进口”“1月/1至1月”表头
    # 位于表格最上方，而 HS50 章可能在第 60 多行，固定回看 10 行会漏掉表头。
    labels: list[str] = []
    for column in range(frame.shape[1]):
        parts = [normalize(frame.iat[row, column]) for row in range(0, first_data_row)]
        labels.append("|".join(part for part in parts if part))
    return labels


def select_flow_columns(labels: list[str], sheet_text: str) -> dict[str, int]:
    selected: dict[str, int] = {}
    for flow, words in {"import": ("进口", "IMPORT"), "export": ("出口", "EXPORT")}.items():
        candidates = []
        for index, label in enumerate(labels):
            if not any(word in label for word in words):
                continue
            # 章号/标题所在的“类章”标签列不是金额列。官方表把整段标题
            # “（4）2024年1月进出口商品类章总值表（美元值）”放进标签列，会带进
            # 出口/进口/1月/美元 等词而被误当成金额列；含“章/类章/总值表”一律排除。
            if any(word in label for word in ("章", "类章", "总值表")):
                continue
            if any(word in label for word in ("累计", "1至", "1-", "YTD", "CUMULATIVE")):
                continue
            # 当月金额列必须带月份标记（“N月”/当月/本月）。类章表里“累计比去年同期±％”
            # 的对比列同样以“出口/进口”作表头，但不含月份，靠这一条把它排除，避免误判为
            # 金额列导致“当月列不唯一”。
            monthly = any(word in label for word in ("当月", "本月", "MONTH", "CURRENT"))
            has_month = monthly or bool(re.search(r"(1[0-2]|[1-9])月", label))
            if not has_month:
                continue
            usd = any(word in label for word in ("美元", "USD", "US$"))
            candidates.append((monthly, usd, index, label))
        if not candidates:
            raise ValueError(f"无法定位{flow}当月列")
        candidates.sort(reverse=True)
        best = candidates[0]
        if len(candidates) > 1 and candidates[1][:2] == best[:2]:
            raise ValueError(f"{flow}当月列不唯一: {[item[3] for item in candidates]}")
        selected[flow] = best[2]
    unit_multiplier(sheet_text + "|" + "|".join(labels))
    return selected


def parse_frame(frame: pd.DataFrame, source: str = "") -> dict[str, dict[int, int]]:
    chapter_rows = [(index, chapter_from_row(row)) for index, row in frame.iterrows()]
    chapter_rows = [(index, chapter) for index, chapter in chapter_rows if chapter in CHAPTERS]
    counts = {chapter: sum(found == chapter for _, found in chapter_rows) for chapter in CHAPTERS}
    missing = [chapter for chapter, count in counts.items() if count == 0]
    duplicates = [chapter for chapter, count in counts.items() if count > 1]
    if missing:
        raise ValueError(f"{source} 缺少 HS 章节: {missing}")
    if duplicates:
        raise ValueError(f"{source} HS 章节重复: {duplicates}")
    first_data_row = min(index for index, _ in chapter_rows)
    labels = column_labels(frame, first_data_row)
    sheet_text = "|".join(normalize(value) for value in frame.iloc[: first_data_row + 1].to_numpy().flat)
    multiplier = unit_multiplier(sheet_text + "|" + "|".join(labels))
    columns = select_flow_columns(labels, sheet_text)
    result = {"import": {}, "export": {}}
    for row_index, chapter in chapter_rows:
        for flow, column in columns.items():
            number = parse_number(frame.iat[row_index, column])
            if number is None:
                raise ValueError(f"{source} HS {chapter} 的 {flow} 值不是数字")
            result[flow][chapter] = int(
                (number * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
    for flow in result:
        if set(result[flow]) != set(CHAPTERS):
            raise ValueError(f"{source} {flow} 不包含完整 HS50-63")
    return result


def parse_report(path: Path) -> dict[str, dict[int, int]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return parse_export_csv(path)
    if suffix in {".xls", ".xlsx", ".xlsm"}:
        sheets = pd.read_excel(path, sheet_name=None, header=None)
    elif suffix in {".html", ".htm"}:
        sheets = {f"table-{index}": table for index, table in enumerate(pd.read_html(path))}
    else:
        raise ValueError(f"不支持的月报格式: {path}")
    errors = []
    for name, frame in sheets.items():
        try:
            return parse_frame(frame, f"{path.name}/{name}")
        except ValueError as exc:
            errors.append(str(exc))
    raise ValueError(f"{path} 没有可解析的美元章别当月表: {'; '.join(errors)}")


def read_export_csv(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return pd.read_csv(path, encoding=encoding, dtype=str).dropna(axis=1, how="all")
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别 CSV 编码: {path}")


def parse_export_csv_records(path: Path) -> list[dict[str, Any]]:
    frame = read_export_csv(path)
    aliases = {
        "month": ("数据年月",), "code": ("商品编码",), "name": ("商品名称",),
    }
    found: dict[str, str] = {}
    for key, names in aliases.items():
        for column in frame.columns:
            if normalize(column) in names:
                found[key] = column
                break
    if set(found) != set(aliases):
        raise ValueError(f"{path.name} 缺少官方导出字段")
    amount_columns = [column for column in frame.columns if column not in found.values()]
    if len(amount_columns) != 1:
        raise ValueError(f"{path.name} 金额列不唯一: {amount_columns}")
    amount_column = amount_columns[0]
    multiplier = unit_multiplier(str(amount_column))
    month_column = found["month"]
    frame[month_column] = frame[month_column].astype(str).str.replace(r"\.0$", "", regex=True)
    if not frame[month_column].map(lambda value: bool(re.fullmatch(r"20\d{4}", value))).all():
        raise ValueError(f"{path.name} 数据年月不是 YYYYMM")
    records = []
    for month, group in frame.groupby(month_column, sort=True):
        values: dict[int, int] = {}
        for _, row in group.iterrows():
            code = re.sub(r"\D", "", str(row[found["code"]]))
            if not code or int(code) not in CHAPTERS:
                continue
            chapter = int(code)
            if chapter in values:
                raise ValueError(f"{path.name} {month} HS {chapter} 重复")
            number = parse_number(row[amount_column])
            if number is None:
                raise ValueError(f"{path.name} {month} HS {chapter} 金额不是数字")
            values[chapter] = int((number * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if set(values) != set(CHAPTERS):
            raise ValueError(f"{path.name} {month} 缺少 HS 章节: {sorted(set(CHAPTERS) - set(values))}")
        records.append({"year": int(month[:4]), "month": int(month[4:]), "values": values})
    return records


def parse_export_csv(path: Path, flow: str | None = None) -> dict[str, dict[int, int]]:
    records = parse_export_csv_records(path)
    if len(records) != 1:
        raise ValueError(f"{path.name} 包含 {len(records)} 个月，单月解析接口不适用")
    if flow is None:
        raise ValueError(f"{path.name} 的官方导出格式不含进出口方向")
    return {flow: records[0]["values"]}


def build_source_records(import_sources: list[Path], export_sources: list[Path]) -> dict[str, list[dict]]:
    records: dict[str, list[dict]] = {"import": [], "export": []}
    for flow, paths in (("import", import_sources), ("export", export_sources)):
        by_month = {}
        for path in paths:
            for record in parse_export_csv_records(path):
                key = record["year"], record["month"]
                if key in by_month:
                    raise ValueError(f"{flow} 月份重复: {key}")
                by_month[key] = record
        records[flow] = [by_month[key] for key in sorted(by_month)]
    if {(r["year"], r["month"]) for r in records["import"]} != {
        (r["year"], r["month"]) for r in records["export"]
    }:
        raise ValueError("进出口月份不一致")
    return records


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.select("a[href]"):
        url = urljoin(base_url, anchor.get("href", ""))
        hostname = urlparse(url).hostname or ""
        if hostname.endswith("customs.gov.cn"):
            links.append((normalize(anchor.get_text(" ", strip=True)), force_http_customs(url)))
    return links


def classify_page(html: str, status: int | None = None) -> str:
    text = normalize(html)
    if status in {502, 504}:
        return f"http_{status}"
    if any(word in text for word in ("验证码", "滑块", "CAPTCHA", "人机验证")):
        return "captcha"
    if "$_TS" in text or "知道创宇" in text or "_$BL()" in text or "_$_D()" in text:
        if status == 412 or "$_TS" in text or "_$BL()" in text or "_$_D()" in text:
            return "js_challenge"
    if status in {412, 502, 504} or re.search(r"\b(412|502|504)\b", text):
        return f"http_{status or re.search(r'(412|502|504)', text).group(1)}"
    visible = BeautifulSoup(html, "html.parser").get_text("", strip=True) if html else ""
    if not visible or len(html) < 120:
        return "blank"
    return "normal"


def is_challenge(html: str) -> bool:
    return classify_page(html) in {"js_challenge", "blank"}


def _is_browser_closed(exc: Exception) -> bool:
    """判断异常是否为浏览器/页面崩溃或关闭（需重建会话）。"""
    signature = f"{type(exc).__name__}: {exc}"
    return any(marker in signature for marker in (
        "TargetClosedError", "has been closed", "Target closed",
        "Connection closed", "browser has been closed", "crash",
    ))


class OfficialSite:
    """Persistent real-Chrome session with bounded state-based retries."""

    def __init__(self, headless: bool = False, max_retries: int = 3,
                 direct_bypass: bool = True, gateway: str | None = None):
        # 无头 Linux(容器/服务器,无 DISPLAY/WAYLAND)上有头模式必然启动失败,自动转无头。
        if (not headless and not IS_WINDOWS and not IS_MACOS
                and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")):
            print("Mainland China: 未检测到图形环境,浏览器自动使用无头模式", flush=True)
            headless = True
        self.headless = headless
        self.max_retries = max_retries
        self.direct_bypass = direct_bypass
        self.gateway = gateway
        self._playwright = self._browser = self._context = self._page = None
        self.responses: list[dict[str, Any]] = []

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("自动采集需要已安装的 Playwright") from exc
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._launch_args = []
        if self.direct_bypass:
            rules, resolved, gateway = prepare_direct_bypass(self.gateway)
            self._launch_args = [
                "--no-proxy-server",
                f"--host-resolver-rules={rules}",
                "--disable-blink-features=AutomationControlled",
            ]
            print(
                f"Mainland China: 海关流量绕过 TUN 直连（网关 {gateway}）；"
                f"解析 {[(d, ips[0]) for d, ips in resolved.items()]}",
                flush=True,
            )
        self._playwright = sync_playwright().start()
        self._launch_browser()
        return self

    def _launch_browser(self) -> None:
        try:
            self._browser = self._playwright.chromium.launch(
                channel="chrome", headless=self.headless, args=self._launch_args,
            )
        except Exception:
            # 环境里没有真实 Chrome(如容器/WSL)时退回 Playwright 自带 Chromium。
            self._browser = self._playwright.chromium.launch(
                headless=self.headless, args=self._launch_args,
            )
        context_options: dict[str, Any] = {
            "accept_downloads": True,
            "ignore_https_errors": True,
        }
        if STORAGE_STATE_PATH.exists():
            context_options["storage_state"] = str(STORAGE_STATE_PATH)
        self._context = self._browser.new_context(**context_options)
        self._page = self._context.new_page()
        self._page.on("response", self._record_response)

    def _save_storage_state(self) -> None:
        try:
            temporary = STORAGE_STATE_PATH.with_suffix(".json.part")
            self._context.storage_state(path=str(temporary))
            temporary.replace(STORAGE_STATE_PATH)
        except Exception:
            pass

    def restart_browser(self) -> None:
        """浏览器/页面崩溃(TargetClosedError)后重建会话，长时间全量抓取时保持存活。"""
        self._save_storage_state()
        for closer in (
            lambda: self._context.close() if self._context else None,
            lambda: self._browser.close() if self._browser else None,
        ):
            try:
                closer()
            except Exception:
                pass
        self._context = self._browser = self._page = None
        self._launch_browser()
        print("Mainland China: 浏览器已崩溃，已重建会话后继续", flush=True)

    def __exit__(self, *_):
        if self._context:
            self._save_storage_state()
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            self._playwright.stop()

    def _record_response(self, response) -> None:
        self.responses.append({"url": response.url, "status": response.status})
        self.responses = self.responses[-200:]

    def get_page(self, url: str, ready_text: str | None = None) -> PageResult:
        """打开 url 并处理 412 Challenge。

        ``ready_text``：海关文章页（如美元类章表）的正文是 goto 之后由 JS 异步注入的，
        commit 时只有 <head>。给定该标记（如“蚕丝”）后，只有当正文里出现它才算就绪，
        否则继续等待正文渲染，避免拿到空壳页。
        """
        last: PageResult | None = None
        for attempt in range(self.max_retries):
            start = len(self.responses)
            try:
                response = self._page.goto(url, wait_until="commit", timeout=35_000)
                status = response.status if response else None
                # 需要等待正文异步注入的文章页（ready_text）放宽到 120 秒，
                # 直连海关站点时整页（含大表格与上百个静态资源）渲染较慢。
                deadline = time.monotonic() + (120 if ready_text else 60)
                challenge_seen = False
                while True:
                    try:
                        html = self._page.content()
                    except Exception as exc:
                        if time.monotonic() >= deadline:
                            last = PageResult(
                                self._page.url, "", "network_timeout", status,
                                [*self.responses[start:], {"error": str(exc)}],
                            )
                            break
                        self._page.wait_for_timeout(250)
                        continue
                    state = classify_page(html, None if challenge_seen else status)
                    last = PageResult(self._page.url, html, state, status, self.responses[start:])
                    if state == "captcha":
                        raise FetchError("captcha", f"页面明确要求验证码: {self._page.url}")
                    if state in ("http_502", "http_504"):
                        break  # 源站/回源硬故障，重试或失败
                    if ready_text is not None:
                        # 只以业务标记（如“蚕丝”/“类章总值表”）作为就绪判据：正文由多段
                        # AJAX 异步注入，commit 时只有 <head> 空壳（会被判为 blank），必须
                        # 一直轮询到标记出现，不能因空壳/挑战中间态就重载，否则会打断渲染。
                        if ready_text in html:
                            try:
                                self._page.wait_for_load_state("networkidle", timeout=8_000)
                            except Exception:
                                pass
                            try:
                                html = self._page.content()
                                last = PageResult(self._page.url, html, state, status, self.responses[start:])
                            except Exception:
                                pass
                            return last
                        challenge_seen = challenge_seen or state == "js_challenge"
                        if time.monotonic() >= deadline:
                            break
                        self._page.wait_for_timeout(1_000)
                        continue
                    if state == "normal":
                        return last
                    waiting_on_challenge = state == "js_challenge" or (
                        challenge_seen and state == "blank"
                    )
                    if not waiting_on_challenge or time.monotonic() >= deadline:
                        break
                    challenge_seen = challenge_seen or state == "js_challenge"
                    self._page.wait_for_timeout(1_000)
            except FetchError:
                raise
            except Exception as exc:
                last = PageResult(url, "", "network_timeout", None, self.responses[start:])
                last.responses.append({"error": str(exc)})
                if _is_browser_closed(exc):
                    try:
                        self.restart_browser()
                    except Exception:
                        pass
            if attempt + 1 < self.max_retries:
                time.sleep((attempt + 1) * 2)
        state = last.state if last else "network_timeout"
        detail = ""
        if last and last.responses and last.responses[-1].get("error"):
            detail = f"；底层错误: {last.responses[-1]['error']}"
        raise FetchError(state, f"页面在 {self.max_retries} 次尝试后仍不可用: {url}{detail}")

    def get_html(self, url: str) -> str:
        return self.get_page(url).html

    def download(self, url: str, destination: Path) -> None:
        url = force_http_customs(url)
        # 必须在页面里用 fetch 下载：只有 Chrome 进程带 --no-proxy-server/
        # --host-resolver-rules 直连配置；Playwright 的 context.request 走驱动进程
        # 网络栈，会被 Clash TUN 劫持到境外出口后被 CDN 掐断（socket hang up）。
        encoded = self._page.evaluate(
            """async (url) => {
                const response = await fetch(url, {credentials: 'include'});
                if (!response.ok) throw new Error('HTTP ' + response.status);
                const bytes = new Uint8Array(await response.arrayBuffer());
                let binary = '';
                for (let i = 0; i < bytes.length; i += 0x8000) {
                    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
                }
                return btoa(binary);
            }""",
            url,
        )
        atomic_write_excel(destination, base64.b64decode(encoded))

    def click_download(self, selector: str, destination: Path) -> str:
        with self._page.expect_download(timeout=30_000) as info:
            self._page.locator(selector).click()
        download = info.value
        temporary = destination.with_suffix(destination.suffix + ".part")
        destination.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(str(temporary))
        validate_excel(temporary.read_bytes())
        temporary.replace(destination)
        return download.url


def validate_excel(content: bytes) -> None:
    if content[:4] not in (b"\xd0\xcf\x11\xe0", b"PK\x03\x04"):
        raise FetchError("invalid_attachment", "附件不是 Excel 文件")


def atomic_write_excel(destination: Path, content: bytes) -> None:
    validate_excel(content)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(content)
    temporary.replace(destination)


def atomic_write_text(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(destination)


def discover_year_links(html: str, base_url: str, start_year: int, end_year: int) -> dict[int, str]:
    # 年份选择器链接的锚文本恰好是四位年份（如“2025”），指向该年月报网格页；而“2025年
    # 12月统计月报”这类最新一期链接文本也含年份，若用模糊匹配并按出现顺序覆盖，当前年份
    # 会被这类文章链接顶掉（曾导致 2025 指向单篇文章而抓不到整年）。故：精确年份文本优先，
    # 仅在某年缺精确链接时用模糊匹配兜底。
    exact: dict[int, str] = {}
    loose: dict[int, str] = {}
    for text, url in extract_links(html, base_url):
        exact_match = re.fullmatch(r"(20\d{2})", text)
        if exact_match:
            year = int(exact_match.group(1))
            if start_year <= year <= end_year:
                exact[year] = url
            continue
        loose_match = re.search(r"(20\d{2})", text)
        if loose_match:
            year = int(loose_match.group(1))
            if start_year <= year <= end_year:
                loose.setdefault(year, url)
    return {**loose, **exact}


def discover_month_links(html: str, base_url: str, year: int) -> dict[int, str]:
    result = {}
    for text, url in extract_links(html, base_url):
        match = re.search(r"(?:^|[^0-9])(1[0-2]|0?[1-9])月", text)
        if match:
            result[int(match.group(1))] = url
            continue
        match = re.search(rf"{year}[-年./](1[0-2]|0?[1-9])", text)
        if match:
            result[int(match.group(1))] = url
    return result


def discover_chapter_usd_links(html: str, base_url: str, year: int) -> dict[int, str]:
    """在年度/导航页上定位「进出口商品类章总值表（美元值）」的每月文章链接。

    年度页把 ~18 种报表 × 12 个月排成表格，且人民币、美元两套并存。用锚点的
    ``title`` 属性精确匹配「类章总值表 + 美元」这一行，比按表格位置猜稳得多。
    返回 {月: 该月美元类章表 URL}。
    """
    soup = BeautifulSoup(html, "html.parser")
    result: dict[int, str] = {}
    for anchor in soup.select("a[href]"):
        title = normalize(anchor.get("title", "") or anchor.get_text(" ", strip=True))
        if "类章总值表" not in title and "章别总值表" not in title:
            continue
        if "美元" not in title:
            continue
        if "累计" in title or "1至" in title:
            continue
        month_match = re.search(r"(1[0-2]|[1-9])月", title)
        if not month_match:
            continue
        url = urljoin(base_url, anchor.get("href", ""))
        if not (urlparse(url).hostname or "").endswith("customs.gov.cn"):
            continue
        result[int(month_match.group(1))] = force_http_customs(url)
    return result


def discover_usd_assets(html: str, base_url: str) -> dict[str, list[str]]:
    links = extract_links(html, base_url)
    return {
        "usd_pages": [url for text, url in links if "美元" in text or "USD" in text],
        "excel": [url for _, url in links if re.search(r"\.xlsx?(?:$|[?#])", url, re.I)],
    }


def excel_response_urls(responses: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(
        item["url"] for item in responses
        if item.get("url") and re.search(r"\.xlsx?(?:$|[?#])", item["url"], re.I)
    ))


def compare_sources(xls: dict[str, dict[int, int]], html: dict[str, dict[int, int]]) -> list[dict[str, Any]]:
    differences = []
    for flow in ("import", "export"):
        for chapter in CHAPTERS:
            if xls[flow][chapter] != html[flow][chapter]:
                differences.append({
                    "flow": flow, "chapter": chapter,
                    "xls": xls[flow][chapter], "html": html[flow][chapter],
                })
    return differences


def _manifest_entries() -> dict[str, dict[str, Any]]:
    if not MANIFEST_PATH.exists():
        return {}
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    items = raw.get("months", raw) if isinstance(raw, dict) else raw
    return {f"{int(item['year']):04d}-{int(item['month']):02d}": item for item in items}


def save_manifest(entries: dict[str, dict[str, Any]]) -> None:
    payload = {"version": 2, "months": [entries[key] for key in sorted(entries)]}
    atomic_write_text(MANIFEST_PATH, json.dumps(payload, ensure_ascii=False, indent=2))


def verified_cache(item: dict[str, Any]) -> Path | None:
    if item.get("source_type") in {"existing_data", "existing_data_totals"}:
        # 由既有正式 CSV 播种的条目无对应报告文件，仅作真值标记。
        return HERE if item.get("verified") and (item.get("values") or item.get("totals")) else None
    if not item.get("verified") or not item.get("path") or not item.get("sha256"):
        return None
    path = HERE / item["path"]
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
        return None
    if item.get("source_type") == "english_bulletin":
        # 英文站页面是千美元展示表，解析值已存 manifest；凭缓存页 sha + 已存值即为已验证
        return path if item.get("values") else None
    if item.get("source_type") == "legacy_baseline":
        export_path = HERE / item.get("export_path", "")
        if (
            not export_path.exists()
            or hashlib.sha256(export_path.read_bytes()).hexdigest() != item.get("export_sha256")
        ):
            return None
        return path if item.get("values") else None
    try:
        parsed = parse_report(path)
    except (ValueError, OSError):
        return None
    return path if set(parsed) == {"import", "export"} else None


def _read_legacy_output(path: Path) -> dict[tuple[int, int], dict[int, int]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or len(rows[0]) != 17:
        raise ValueError(f"{path} 不是 17 列中国历史输出")
    result: dict[tuple[int, int], dict[int, int]] = {}
    for row in rows[1:]:
        if len(row) != 17:
            raise ValueError(f"{path} 存在非 17 列数据行")
        year, month = int(row[0]), int(row[1])
        values = [int(value) for value in row[2:16]]
        total = int(row[16])
        if any(value < 0 for value in values) or total != sum(values):
            raise ValueError(f"{path} {year}-{month:02d} 总计或金额无效")
        key = (year, month)
        if key in result:
            raise ValueError(f"{path} 月份重复: {year}-{month:02d}")
        result[key] = dict(zip(CHAPTERS, values, strict=True))
    if not result:
        raise ValueError(f"{path} 没有数据")
    expected = _months_between(min(result)[0], max(result))
    if set(result) != set(expected):
        raise ValueError(f"{path} 月份不连续")
    return result


def bootstrap_legacy_baseline(entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if entries or not LEGACY_IMPORT.exists() or not LEGACY_EXPORT.exists():
        return entries
    imports = _read_legacy_output(LEGACY_IMPORT)
    exports = _read_legacy_output(LEGACY_EXPORT)
    if set(imports) != set(exports):
        raise ValueError("中国历史进出口基线月份不一致")
    import_sha = hashlib.sha256(LEGACY_IMPORT.read_bytes()).hexdigest()
    export_sha = hashlib.sha256(LEGACY_EXPORT.read_bytes()).hexdigest()
    fetched_at = datetime.now(timezone.utc).isoformat()
    for year, month in sorted(imports):
        key = f"{year:04d}-{month:02d}"
        entries[key] = {
            "year": year,
            "month": month,
            "year_url": None,
            "detail_url": None,
            "usd_page_url": None,
            "download_url": None,
            "path": str(LEGACY_IMPORT.relative_to(HERE)),
            "export_path": str(LEGACY_EXPORT.relative_to(HERE)),
            "sha256": import_sha,
            "export_sha256": export_sha,
            "fetched_at": fetched_at,
            "source_type": "legacy_baseline",
            "unit": "USD",
            "multiplier": 1,
            "online_status": "cached",
            "stale": True,
            "verified": True,
            "failure_type": "online_unavailable",
            "error": "官网不可用，使用经完整性校验的既有正式发布基线",
            "cross_check": {"status": "legacy_output_totals_verified", "differences": []},
            "values": {"import": imports[(year, month)], "export": exports[(year, month)]},
        }
    save_manifest(entries)
    return entries


def seed_entries_from_existing(csv_path: Path) -> dict[str, dict[str, Any]]:
    """把既有正式输出播种为已验证 manifest 条目。

    用于 --refresh data：data/ 里已有的月份不再抓取。与 legacy_baseline 同样语义——
    兼容旧的章级长表和新的月度五列表；章级缓存存在时仍优先保留章级值。
    """
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    if fieldnames == ["年月", "年", "月", "进口额", "出口额"]:
        fetched_at = datetime.now(timezone.utc).isoformat()
        return {
            f"{int(row['年']):04d}-{int(row['月']):02d}": {
                "year": int(row["年"]),
                "month": int(row["月"]),
                "year_url": None,
                "detail_url": None,
                "usd_page_url": None,
                "download_url": None,
                "path": None,
                "sha256": None,
                "fetched_at": fetched_at,
                "source_type": "existing_data_totals",
                "unit": "USD",
                "multiplier": 1,
                "online_status": "cached",
                "stale": False,
                "verified": True,
                "failure_type": None,
                "error": None,
                "cross_check": {"status": "seeded_from_existing_csv_totals", "differences": []},
                "totals": {"import": int(row["进口额"]), "export": int(row["出口额"])},
            }
            for row in rows
        }
    if fieldnames != ["年", "月", "章", "进口额", "出口额"]:
        raise ValueError(f"{csv_path} 不是支持的中国最终输出格式: {fieldnames}")

    name_to_chapter = dict(zip(OUTPUT_COLUMNS[2:-1], CHAPTERS, strict=True))
    total_name = OUTPUT_COLUMNS[-1]
    months: dict[tuple[int, int], dict[str, dict[int, int]]] = {}
    totals: dict[tuple[int, int], dict[str, int]] = {}
    for row in rows:
        year, month = int(row["年"]), int(row["月"])
        key = (year, month)
        if row["章"] == total_name:
            totals[key] = {"import": int(row["进口额"]), "export": int(row["出口额"])}
            continue
        chapter = name_to_chapter.get(row["章"])
        if chapter is None:
            raise ValueError(f"{csv_path} 存在未识别的章: {row['章']}")
        flows = months.setdefault(key, {"import": {}, "export": {}})
        flows["import"][chapter] = int(row["进口额"])
        flows["export"][chapter] = int(row["出口额"])
    if not months:
        raise ValueError(f"{csv_path} 没有数据")
    fetched_at = datetime.now(timezone.utc).isoformat()
    entries: dict[str, dict[str, Any]] = {}
    for (year, month), flows in sorted(months.items()):
        for flow in ("import", "export"):
            if set(flows[flow]) != set(CHAPTERS):
                raise ValueError(f"{csv_path} {year}-{month:02d} {flow} 章不完整")
            expected_total = totals.get((year, month), {}).get(flow)
            if expected_total is not None and expected_total != sum(flows[flow].values()):
                raise ValueError(f"{csv_path} {year}-{month:02d} {flow} 总计与各章之和不符")
        entries[f"{year:04d}-{month:02d}"] = {
            "year": year,
            "month": month,
            "year_url": None,
            "detail_url": None,
            "usd_page_url": None,
            "download_url": None,
            "path": None,
            "sha256": None,
            "fetched_at": fetched_at,
            "source_type": "existing_data",
            "unit": "USD",
            "multiplier": 1,
            "online_status": "cached",
            "stale": False,
            "verified": True,
            "failure_type": None,
            "error": None,
            "cross_check": {"status": "seeded_from_existing_csv", "differences": []},
            "values": flows,
        }
    return entries


def write_failure_artifacts(site: OfficialSite | None, year: int, month: int, error: Exception, url: str = "") -> None:
    directory = FAILURES_DIR / f"{year:04d}-{month:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    html = ""
    if site and site._page:
        try:
            html = site._page.content()
            site._page.screenshot(path=str(directory / "screenshot.png"), full_page=True)
        except Exception:
            pass
    atomic_write_text(directory / "page.html", html)
    diagnostics = {
        "year": year, "month": month, "url": url,
        "failure_type": getattr(error, "kind", type(error).__name__),
        "error": str(error), "captured_at": datetime.now(timezone.utc).isoformat(),
        "responses": site.responses[-100:] if site else [],
    }
    atomic_write_text(directory / "diagnostics.json", json.dumps(diagnostics, ensure_ascii=False, indent=2))


def _parse_and_cache_month(site: OfficialSite, year: int, month: int, year_url: str, detail_url: str) -> dict[str, Any]:
    # detail_url 本身即「进出口商品类章总值表（美元值）」页；正文由 JS 异步注入，
    # 用 ready_text 等 HS50「蚕丝」出现，确保拿到完整表格而非空壳 <head>。
    detail = site.get_page(detail_url, ready_text="蚕丝")
    page_path = PAGES_DIR / str(year) / f"{year}-{month:02d}.html"
    atomic_write_text(page_path, detail.html)
    usd_text = normalize(detail.html)
    if "人民币" in usd_text and not any(word in usd_text for word in ("美元", "USD")):
        raise FetchError("rmb_only", "月份页面只能确认人民币版本")
    assets = discover_usd_assets(detail.html, detail.url)
    excel_urls = list(dict.fromkeys(assets["excel"] + excel_response_urls(detail.responses)))

    # 优先用页面直链的 .xls 附件：它是全精度（.xls 到美元位）的权威版本，网页表格是
    # 四舍五入到千美元的展示版。两者相差仅在末位，不做交叉比对以免“源冲突”。
    xls_path = xls_values = download_url = None
    last_error: Exception | None = None
    candidate_path: Path | None = None
    for candidate in excel_urls:
        suffix = Path(urlparse(candidate).path).suffix.lower() or ".xls"
        final_path = REPORTS_DIR / str(year) / f"{year}-{month:02d}{suffix}"
        candidate_path = final_path.with_name(f"{final_path.stem}.candidate{final_path.suffix}")
        try:
            site.download(candidate, candidate_path)
            parsed = parse_report(candidate_path)
            xls_path, xls_values, download_url = final_path, parsed, candidate
            break
        except Exception as exc:
            # 曾因只捕获 (FetchError, ValueError, OSError) 漏掉 Playwright Error，
            # 导致下载失败直接冲出本函数、网页表格回退从未执行。除浏览器崩溃
            # （需外层重建会话重试）外，任何下载/解析失败都应继续走回退链。
            if _is_browser_closed(exc):
                raise
            last_error = exc
            candidate_path.unlink(missing_ok=True)
            brief = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            print(f"Mainland China: {year}-{month:02d} .xls 直链下载失败（{brief}），尝试回退", flush=True)

    if not xls_values:
        # 个别版式无直链 .xls 时，退回点击下载按钮（跳过全站“下载中心”导航链接）
        locators = site._page.locator("a,button,input[type=button],input[type=submit]")
        for index in range(locators.count()):
            locator = locators.nth(index)
            label = normalize((locator.inner_text() or "") + (locator.get_attribute("value") or ""))
            if not any(word in label for word in ("下载", "EXCEL", "XLS")) or "下载中心" in label:
                continue
            final_path = REPORTS_DIR / str(year) / f"{year}-{month:02d}.xls"
            candidate_path = final_path.with_name(f"{final_path.stem}.candidate{final_path.suffix}")
            try:
                with site._page.expect_download(timeout=30_000) as info:
                    locator.click()
                download = info.value
                candidate_path.parent.mkdir(parents=True, exist_ok=True)
                download.save_as(str(candidate_path))
                validate_excel(candidate_path.read_bytes())
                parsed = parse_report(candidate_path)
                xls_path, xls_values, download_url = final_path, parsed, download.url
                break
            except Exception as exc:
                if _is_browser_closed(exc):
                    raise
                last_error = exc
                candidate_path.unlink(missing_ok=True)

    if xls_values:
        candidate_path.replace(xls_path)
        source_path, values, source_type = xls_path, xls_values, "xls"
        cross_check: dict[str, Any] = {"status": "xls_only", "differences": []}
    else:
        # 退回解析已渲染的网页类章表（千美元展示精度）
        try:
            values = parse_report(page_path)
        except (ValueError, OSError) as exc:
            raise last_error or exc
        if set(values) != {"import", "export"}:
            raise FetchError("no_usd_data", "网页表格未包含完整进出口方向")
        source_path, source_type = page_path, "html"
        cross_check = {"status": "html_only", "differences": []}
        if excel_urls:
            print(f"Mainland China: {year}-{month:02d} 已回退网页表格（千美元精度），下次运行将尝试升级回 .xls 全精度", flush=True)

    return {
        "year": year, "month": month, "year_url": year_url, "detail_url": detail_url,
        "usd_page_url": detail.url, "download_url": download_url,
        "path": str(source_path.relative_to(HERE)),
        "page_path": str(page_path.relative_to(HERE)),
        "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_type": source_type, "unit": "USD", "multiplier": 1,
        "online_status": "fresh", "verified": True, "failure_type": None,
        "error": None, "cross_check": cross_check, "values": values,
    }


def _months_between(start_year: int, latest: tuple[int, int] | None) -> list[tuple[int, int]]:
    if latest is None or latest < (start_year, 1):
        return []
    months = []
    year, month = start_year, 1
    while (year, month) <= latest:
        months.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def retry_failed_months(
    site: OfficialSite,
    entries: dict[str, dict],
    discovered: dict[tuple[int, int], tuple[str, str]],
    rounds: int = 2,
) -> list[tuple[int, int]]:
    pending = [
        (year, month) for year, month in sorted(discovered)
        if entries.get(f"{year:04d}-{month:02d}", {}).get("online_status") == "failed"
        and not verified_cache(entries.get(f"{year:04d}-{month:02d}", {}))
    ]
    for retry_round in range(1, rounds + 1):
        if not pending:
            break
        print(
            f"Mainland China: 补抓第 {retry_round}/{rounds} 轮，重试 {len(pending)} 个失败月份",
            flush=True,
        )
        try:
            site.restart_browser()
        except Exception:
            pass
        remaining: list[tuple[int, int]] = []
        for year, month in pending:
            key = f"{year:04d}-{month:02d}"
            year_url, detail_url = discovered[(year, month)]
            print(f"Mainland China: retrying {key} {detail_url}", flush=True)
            try:
                entries[key] = _parse_and_cache_month(site, year, month, year_url, detail_url)
            except Exception as exc:
                write_failure_artifacts(site, year, month, exc, detail_url)
                entries[key] = {
                    "year": year, "month": month, "year_url": year_url,
                    "detail_url": detail_url, "online_status": "failed", "verified": False,
                    "failure_type": getattr(exc, "kind", type(exc).__name__), "error": str(exc),
                }
                remaining.append((year, month))
            else:
                print(f"Mainland China: [RECOVERED] {key} 补抓成功", flush=True)
            save_manifest(entries)
        pending = remaining
        if pending and retry_round < rounds:
            time.sleep(retry_round * 5)
    return pending


def collect_online(start_year: int, end_year: int, headless: bool, refresh_all: bool,
                   direct_bypass: bool = True, gateway: str | None = None,
                   fresh: bool = False, trust_existing: bool = False) -> tuple[dict[str, dict], tuple[int, int] | None]:
    entries = {} if fresh else _manifest_entries()
    with OfficialSite(headless=headless, direct_bypass=direct_bypass, gateway=gateway) as site:
        print(f"Mainland China: opening navigation {NAV_URL}", flush=True)
        try:
            nav = site.get_page(NAV_URL, ready_text="类章总值表")
        except Exception as exc:
            write_failure_artifacts(site, 0, 0, exc, NAV_URL)
            raise
        year_links = discover_year_links(nav.html, nav.url, start_year, end_year)
        if not year_links:
            raise FetchError("navigation", "导航页未发现年份链接")
        discovered: dict[tuple[int, int], tuple[str, str]] = {}
        # 年份发现是自扩展的：NAV_URL 只是种子页（其年份选择器只列出该年及更早），
        # 每个年份页上若再出现未见过的年份链接（新年度网格页或“<年>年<月>月统计
        # 月报”文章），自动加入队列跟进——新年度发布后无需手工更新 NAV_URL。
        queue = sorted(year_links.items())
        seen_years = set(year_links)
        while queue:
            year, year_url = queue.pop(0)
            print(f"Mainland China: discovering {year} {year_url}", flush=True)
            try:
                year_page = site.get_page(year_url, ready_text="类章总值表")
                entries.pop(f"{year:04d}-00", None)
                for month, detail_url in discover_chapter_usd_links(year_page.html, year_page.url, year).items():
                    discovered[(year, month)] = (year_url, detail_url)
                for extra_year, extra_url in discover_year_links(
                        year_page.html, year_page.url, start_year, end_year).items():
                    if extra_year not in seen_years:
                        seen_years.add(extra_year)
                        queue.append((extra_year, extra_url))
                        print(f"Mainland China: 发现新增年份 {extra_year} {extra_url}", flush=True)
                queue.sort()
            except Exception as exc:
                write_failure_artifacts(site, year, 0, exc, year_url)
                entries[f"{year:04d}-00"] = {
                    "year": year, "month": 0, "year_url": year_url,
                    "online_status": "failed", "verified": False,
                    "failure_type": getattr(exc, "kind", type(exc).__name__), "error": str(exc),
                }
        # 目标是抓到「当前月的上一个月」；中文站类章表发布滞后是常态，
        # 差距由英文站 Monthly Bulletin 兜底延伸，这里仅提示现状。
        today = date.today()
        target = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        chinese_latest = max(discovered, default=None)
        if chinese_latest and chinese_latest < target:
            print(
                f"Mainland China: 中文站类章表最新为 {chinese_latest[0]}-{chinese_latest[1]:02d}，"
                f"目标 {target[0]}-{target[1]:02d}，缺口将尝试英文站兜底",
                flush=True,
            )
        known_months = [
            (int(item["year"]), int(item["month"])) for item in entries.values()
            if int(item.get("month", 0)) > 0 and int(item["year"]) <= end_year
        ]
        latest = max([*discovered, *known_months], default=None)
        recent = set(sorted(discovered)[-2:])
        processed_since_restart = 0
        for (year, month), (year_url, detail_url) in sorted(discovered.items()):
            key = f"{year:04d}-{month:02d}"
            print(f"Mainland China: processing {key} {detail_url}", flush=True)
            cached_path = verified_cache(entries.get(key, {}))
            # 英文站兜底月份（千美元舍入）不算「已有」：中文站可达时重抓升级回全精度
            if entries.get(key, {}).get("source_type") == "english_bulletin":
                cached_path = None
            # 2020 起官方提供全精度 .xls；因下载故障回退网页表格（千美元舍入）的
            # 月份同样不算「已有」，下次运行重抓以升级回全精度
            if year >= 2020 and entries.get(key, {}).get("source_type") == "html":
                cached_path = None
            if cached_path and not refresh_all and (trust_existing or (year, month) not in recent):
                entries[key].update({
                    "online_status": "cached", "stale": False,
                    "failure_type": None, "error": None,
                })
                continue
            # 每抓 25 个月主动重建一次浏览器：长时间会话下 Chrome 内存累积会崩溃(曾在
            # 第 44 个月崩溃并使其后全部 TargetClosedError)，主动重启把风险降到最低。
            if processed_since_restart >= 25:
                try:
                    site.restart_browser()
                except Exception:
                    pass
                processed_since_restart = 0
            processed_since_restart += 1
            previous = entries.get(key)
            result = None
            for attempt in range(2):  # 浏览器崩溃时重建后再试一次
                try:
                    result = _parse_and_cache_month(site, year, month, year_url, detail_url)
                    break
                except Exception as exc:
                    write_failure_artifacts(site, year, month, exc, detail_url)
                    last_exc = exc
                    if _is_browser_closed(exc) and attempt == 0:
                        try:
                            site.restart_browser()
                        except Exception:
                            pass
                        processed_since_restart = 1
                        continue
                    break
            if result is not None:
                entries[key] = result
            elif previous and verified_cache(previous):
                previous.update({
                    "online_status": "cached", "stale": True,
                    "failure_type": getattr(last_exc, "kind", type(last_exc).__name__), "error": str(last_exc),
                })
                entries[key] = previous
                brief = str(last_exc).splitlines()[0] if str(last_exc) else type(last_exc).__name__
                print(f"Mainland China: [STALE] {key} 在线抓取失败，沿用既有缓存: {brief}", flush=True)
            else:
                entries[key] = {
                    "year": year, "month": month, "year_url": year_url,
                    "detail_url": detail_url, "online_status": "failed", "verified": False,
                    "failure_type": getattr(last_exc, "kind", type(last_exc).__name__), "error": str(last_exc),
                }
                brief = str(last_exc).splitlines()[0] if str(last_exc) else type(last_exc).__name__
                print(f"Mainland China: [FAILED] {key} 本月抓取失败（无可用缓存）: {brief}", flush=True)
            save_manifest(entries)
            time.sleep(1)
        retry_failed_months(site, entries, discovered)
    save_manifest(entries)
    return entries, latest


ENGLISH_START_YEAR = 2018  # 英文站 Monthly Bulletin 最早只到 2018 年


def collect_english(start_year: int, end_year: int,
                    gateway: str | None = None) -> tuple[dict[str, dict], tuple[int, int] | None]:
    """兜底来源：中文站抓不到的月份用英文站 Monthly Bulletin 表(4)补齐。

    英文站表格是千美元舍入的展示版，中文 .xls 才是全精度权威版，因此只补缺失
    月份、绝不覆盖任何已验证的中文数据；英文兜底月份之后一旦能从中文站抓到，
    collect_online 会自动用全精度版本替换回来。英文站最早只有 2018 年，且个别
    月份缺失（如 2024-09/10 无链接），仍补不到的月份由下游按不完整处理。
    """
    import fetch_mainland_china_english as english

    # 英文站同样被 Clash TUN 劫持后 502：先准备 IP 直连（复用/新增 /24 路由），
    # 失败则按默认网络访问碰运气（如用户已关闭 TUN）。
    try:
        direct_ip = prepare_english_direct(gateway)
        english.set_direct_ip(direct_ip)
        print(f"Mainland China: 英文站直连 english.customs.gov.cn -> {direct_ip}", flush=True)
    except Exception as exc:
        print(f"Mainland China: 英文站直连准备失败，按默认网络访问: {exc}", flush=True)

    entries = bootstrap_legacy_baseline(_manifest_entries())
    current_year = max(end_year, date.today().year)
    discovered: dict[tuple[int, int], str] = {}
    for year in range(max(start_year, ENGLISH_START_YEAR), end_year + 1):
        missing_months = [
            month for month in range(1, 13)
            if not verified_cache(entries.get(f"{year:04d}-{month:02d}", {}))
        ]
        if not missing_months:
            continue
        try:
            links = english.discover_table4_links(year, current_year)
        except Exception as exc:
            print(f"Mainland China: 英文站 {year} 年列表页失败: {exc}", flush=True)
            continue
        for month, url in links.items():
            if month in missing_months:
                discovered[(year, month)] = url

    known_months = [
        (int(item["year"]), int(item["month"])) for item in entries.values()
        if int(item.get("month", 0)) > 0 and int(item["year"]) <= end_year
    ]
    latest = max([*discovered, *known_months], default=None)
    for (year, month), url in sorted(discovered.items()):
        key = f"{year:04d}-{month:02d}"
        print(f"Mainland China: english bulletin 兜底 {key} {url}", flush=True)
        try:
            values, page_path = english.fetch_month_values(year, month, url)
        except Exception as exc:
            entries[key] = {
                "year": year, "month": month, "detail_url": url,
                "online_status": "failed", "verified": False,
                "failure_type": getattr(exc, "kind", type(exc).__name__),
                "error": str(exc),
            }
            continue
        entries[key] = {
            "year": year, "month": month, "year_url": None, "detail_url": url,
            "usd_page_url": url, "download_url": None,
            "path": str(page_path.relative_to(HERE)),
            "sha256": hashlib.sha256(page_path.read_bytes()).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "english_bulletin", "unit": "USD", "multiplier": 1,
            "online_status": "fresh", "verified": True, "failure_type": None,
            "error": None,
            "cross_check": {"status": "english_html_only", "differences": []},
            "values": values,
        }
        save_manifest(entries)
    save_manifest(entries)
    return entries, latest


def collect_offline(start_year: int, end_year: int) -> tuple[dict[str, dict], tuple[int, int] | None]:
    entries = bootstrap_legacy_baseline(_manifest_entries())
    relevant = {
        key: item for key, item in entries.items()
        if start_year <= int(item["year"]) <= end_year
    }
    latest = max(((int(item["year"]), int(item["month"])) for item in relevant.values()), default=None)
    for item in relevant.values():
        item["online_status"] = "cached" if verified_cache(item) else "failed"
    return entries, latest


def build_records(cached: list[tuple[dict, Path]] | dict[str, dict]) -> dict[str, list[dict]]:
    records: dict[str, list[dict]] = {"import": [], "export": []}
    pairs = cached.items() if isinstance(cached, dict) else ((f"{i['year']:04d}-{i['month']:02d}", (i, p)) for i, p in cached)
    for _, value in sorted(pairs):
        if isinstance(value, tuple):
            item, path = value
        else:
            item, path = value, verified_cache(value)
            if path is None:
                continue
        if item.get("totals"):
            for flow in records:
                records[flow].append({
                    "year": int(item["year"]),
                    "month": int(item["month"]),
                    "total": int(item["totals"][flow]),
                })
            continue
        parsed = item.get("values") or parse_report(path)
        parsed = {flow: {int(ch): int(amount) for ch, amount in values.items()} for flow, values in parsed.items()}
        for flow in records:
            if flow not in parsed:
                raise ValueError(f"{item['year']}-{item['month']:02d} 缺少 {flow}")
            records[flow].append({"year": int(item["year"]), "month": int(item["month"]), "values": parsed[flow]})
    return records


def load_cached_reports() -> list[tuple[dict, Path]]:
    entries = _manifest_entries()
    result = []
    for key in sorted(entries):
        path = verified_cache(entries[key])
        if path:
            result.append((entries[key], path))
    if not result:
        raise RuntimeError("离线模式没有已验证缓存")
    return result


def write_output(path: Path, records: dict[str, list[dict]]) -> None:
    imports = {(record["year"], record["month"]): record for record in records["import"]}
    exports = {(record["year"], record["month"]): record for record in records["export"]}
    if set(imports) != set(exports):
        raise ValueError("进出口月份不一致")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["年月", "年", "月", "进口额", "出口额"])
        for year, month in sorted(imports):
            import_record = imports[(year, month)]
            export_record = exports[(year, month)]
            import_total = import_record.get("total")
            export_total = export_record.get("total")
            if import_total is None:
                import_total = sum(int(import_record["values"][chapter]) for chapter in CHAPTERS)
            if export_total is None:
                export_total = sum(int(export_record["values"][chapter]) for chapter in CHAPTERS)
            writer.writerow([f"{year:04d}{month:02d}", year, month, import_total, export_total])
    for attempt in range(3):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 2:
                raise FetchError(
                    "output_locked",
                    f"无法写入 {path}：文件正被其他程序占用（Excel/WPS 打开了？）。"
                    f"请关闭后重跑；本次数据已保留在 {temporary}",
                )
            time.sleep(2)


def _report(entries: dict[str, dict], latest: tuple[int, int] | None, expected: list[tuple[int, int]], published: bool) -> dict[str, Any]:
    categories = {
        "online_success_months": [], "cached_months": [], "failed_without_cache_months": [],
        "rmb_rejected_months": [], "source_conflict_months": [],
    }
    for year, month in expected:
        key = f"{year:04d}-{month:02d}"
        item = entries.get(key, {})
        status = item.get("online_status")
        if status == "fresh":
            categories["online_success_months"].append(key)
        elif status == "cached" and verified_cache(item):
            categories["cached_months"].append(key)
        else:
            categories["failed_without_cache_months"].append(key)
        if item.get("failure_type") == "rmb_only":
            categories["rmb_rejected_months"].append(key)
        if item.get("failure_type") == "source_conflict":
            categories["source_conflict_months"].append(key)
    return {
        **categories,
        "official_latest_month": f"{latest[0]:04d}-{latest[1]:02d}" if latest else None,
        "expected_month_count": len(expected), "published_china_csv": published,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main(output_path: Path = DEFAULT_OUTPUT, *,
         offline: bool = False, browser: bool = True, headed: bool | None = None,
         headless: bool = False, refresh_all: bool = False,
         start_year: int = 2015, end_year: int | None = None,
         import_sources: list[Path] | None = None,
         export_sources: list[Path] | None = None,
         direct_bypass: bool = True, gateway: str | None = None,
         fresh: bool = False, existing_path: Path | None = None) -> dict[str, list[dict]]:
    del browser
    if headed is not None:
        headless = not headed
    if fresh:
        # 完整复现：强制逐月重抓，不使用任何本地缓存或历史基线做替代。
        refresh_all = True
    end_year = end_year or date.today().year
    use_existing = existing_path is not None and existing_path.exists()
    if use_existing:
        # --refresh data：把既有正式 CSV 播种进 manifest，已有月份（含近期）不再抓取。
        seeded = seed_entries_from_existing(existing_path)
        # manifest 里的真实下载缓存优先；但只认缓存文件仍在且校验通过的条目，
        # 否则(如新机器只有 data/ 没有 cache/ 报告文件)会覆盖播种导致全量重抓。
        usable_manifest = {
            key: item for key, item in _manifest_entries().items()
            if verified_cache(item)
        }
        save_manifest({**seeded, **usable_manifest})
        print(f"Mainland China: data 模式,沿用 {existing_path} 已有 {len(seeded)} 个月", flush=True)
    if import_sources or export_sources:
        if not import_sources or not export_sources:
            raise ValueError("必须同时提供进口和出口官方 CSV")
        records = build_source_records(import_sources, export_sources)
        write_output(output_path, records)
        return records
    latest: tuple[int, int] | None = None
    entries: dict[str, dict]
    top_error: Exception | None = None
    chinese_error: Exception | None = None
    english_extended = False  # 英文站把最新月延伸到中文站之后
    try:
        if offline:
            entries, latest = collect_offline(start_year, end_year)
        else:
            # 精度优先：中文站 .xls 全精度是主来源；英文站（千美元舍入）只兜底
            # 中文站失败或缺失的月份。--live-fresh 语义是「中文官网完整复现」，
            # 不做英文站兜底。
            try:
                entries, latest = collect_online(
                    start_year, end_year, headless, refresh_all,
                    direct_bypass=direct_bypass, gateway=gateway, fresh=fresh,
                    trust_existing=use_existing,
                )
            except Exception as exc:
                if fresh:
                    raise
                chinese_error = exc
                latest = None
                print(f"Mainland China: 中文站采集失败，尝试英文站兜底: {exc}", flush=True)
            if not fresh:
                if chinese_error is None:
                    missing = [
                        (year, month) for year, month in _months_between(start_year, latest)
                        if not verified_cache(entries.get(f"{year:04d}-{month:02d}", {}))
                    ]
                    if missing:
                        print(
                            f"Mainland China: 中文站缺 {len(missing)} 个月"
                            f"（如 {missing[0][0]}-{missing[0][1]:02d}），用英文站兜底补齐",
                            flush=True,
                        )
                # 英文站 Monthly Bulletin 发布节奏快于中文站类章 .xls（中文站可能
                # 停在数月前），因此不论中文站是否完整都再查一遍英文站：既回补
                # 中文站缺失/失败的月份，也把覆盖范围延伸到英文站最新已发布月。
                try:
                    entries, latest_english = collect_english(start_year, end_year, gateway=gateway)
                except Exception as exc:
                    if chinese_error is not None:
                        raise chinese_error  # 两边都失败：按中文站失败走既有兜底路径
                    print(f"Mainland China: 英文站兜底失败: {exc}", flush=True)
                else:
                    if latest_english is not None and (latest is None or latest_english > latest):
                        english_extended = True
                        print(
                            f"Mainland China: 英文站已发布至 "
                            f"{latest_english[0]}-{latest_english[1]:02d}，超出中文站"
                            f"{f' {latest[0]}-{latest[1]:02d}' if latest else ''}，以英文站延伸覆盖",
                            flush=True,
                        )
                    candidates = [value for value in (latest, latest_english) if value is not None]
                    latest = max(candidates) if candidates else None
    except Exception as exc:
        if fresh:
            # 完整复现模式下不静默退回历史基线：让失败暴露出来。
            raise
        top_error = exc
        entries = bootstrap_legacy_baseline(_manifest_entries())
        relevant = [(int(item["year"]), int(item["month"])) for item in entries.values() if int(item["year"]) <= end_year]
        latest = max(relevant, default=None)
    expected = _months_between(start_year, latest)
    valid_entries = {
        key: item for key, item in entries.items()
        if (int(item["year"]), int(item["month"])) in expected and verified_cache(item)
    }
    records = build_records(valid_entries)
    available = {(record["year"], record["month"]) for record in records["import"]}
    complete = bool(expected) and available == set(expected)
    partial_path = output_path.with_name(output_path.stem + ".partial.csv")
    if complete:
        write_output(output_path, records)
        partial_path.unlink(missing_ok=True)
    else:
        write_output(partial_path, records)
        print(f"Mainland China: 不完整结果已保留在 {partial_path}", flush=True)
    report = _report(entries, latest, expected, complete)
    if top_error:
        report["top_level_error"] = str(top_error)
        report["top_level_failure_type"] = getattr(top_error, "kind", type(top_error).__name__)
        report["latest_month_source"] = "verified_cache"
        report["official_navigation_available"] = False
    else:
        latest_key = f"{latest[0]:04d}-{latest[1]:02d}" if latest else None
        latest_entry_source = (entries.get(latest_key) or {}).get("source_type") if latest_key else None
        report["latest_month_source"] = (
            "english_bulletin"
            if (chinese_error is not None or english_extended
                or latest_entry_source == "english_bulletin")
            else "official_navigation"
        )
        report["official_navigation_available"] = chinese_error is None
    FETCH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(FETCH_REPORT_PATH, json.dumps(report, ensure_ascii=False, indent=2))
    print(
        "Mainland China: "
        f"fresh={len(report['online_success_months'])}, cached={len(report['cached_months'])}, "
        f"missing={len(report['failed_without_cache_months'])}, published={complete}"
    )
    current_failures = [
        item for item in entries.values()
        if item.get("failure_type") and item.get("online_status") in {"failed", "cached"}
    ]
    if not complete:
        missing = report["failed_without_cache_months"]
        example = f"（如 {missing[0]}）" if missing else ""
        raise FetchError(
            "incomplete",
            f"中国海关月份不完整{example}，未覆盖正式 CSV；部分结果见 {partial_path}",
        )
    # A Chinese-site failure is not a run failure when the official English bulletin
    # successfully fills every month; that is the documented fallback path. Keep the
    # degraded source recorded in the report, but let the orchestrator publish it.
    if chinese_error is not None and not top_error and not current_failures:
        print("Mainland China: 中文主站不可用，完整结果来自英文官方兜底", flush=True)
    if top_error or (not offline and current_failures):
        raise FetchError("partial_failure", "中国海关已用缓存/兜底来源发布，但本次在线采集存在失败")
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh-all", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--headed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--import-source", type=Path, action="append")
    parser.add_argument("--export-source", type=Path, action="append")
    parser.add_argument("--live-fresh", action="store_true",
                        help="完整复现：绕过 Clash TUN 直连海关官网，逐月重抓 start-year 到"
                             "当前最新可得月份，不使用任何本地缓存/历史基线替代")
    parser.add_argument("--gateway", help="物理默认网关 IP（默认自动检测；仅在自动检测失败时指定）")
    parser.add_argument("--no-direct-bypass", action="store_true",
                        help="不做海关流量直连绕过（当 Clash 已关闭或本机可直连官网时使用）")
    parser.add_argument("--cleanup-routes", action="store_true",
                        help="仅删除本工具添加的海关 /32 直连路由后退出")
    args = parser.parse_args()
    if args.cleanup_routes:
        cleanup_direct_bypass(args.gateway)
        raise SystemExit(0)
    main(
        args.output, offline=args.offline,
        headless=args.headless and not args.headed, refresh_all=args.refresh_all,
        start_year=args.start_year, end_year=args.end_year,
        import_sources=args.import_source, export_sources=args.export_source,
        direct_bypass=not args.no_direct_bypass, gateway=args.gateway,
        fresh=args.live_fresh,
    )
