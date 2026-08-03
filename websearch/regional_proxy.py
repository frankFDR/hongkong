# -*- coding: utf-8 -*-
"""为美国和香港数据源启动项目专用的 Mihomo 路由。"""
from __future__ import annotations

import atexit
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.yaml"


class RegionalProxyError(RuntimeError):
    pass


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except FileNotFoundError as exc:
        raise RegionalProxyError(f"代理配置不存在: {path}") from exc
    except yaml.YAMLError as exc:
        raise RegionalProxyError(f"代理配置不是有效 YAML: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RegionalProxyError(f"代理配置顶层必须是映射: {path}")
    return data


def _matching_nodes(proxies: list[dict], patterns: list[str], excluded: list[str]) -> list[str]:
    return [
        str(proxy["name"])
        for proxy in proxies
        if proxy.get("name")
        and any(pattern in str(proxy["name"]) for pattern in patterns)
        and not any(pattern in str(proxy["name"]) for pattern in excluded)
    ]


def build_runtime_config(settings: dict, base_dir: Path = HERE) -> dict:
    clash_path = Path(settings.get("clash_config", "clash.yaml"))
    if not clash_path.is_absolute():
        clash_path = base_dir / clash_path
    clash = _load_yaml(clash_path)
    proxies = clash.get("proxies") or []
    if not isinstance(proxies, list):
        raise RegionalProxyError(f"clash.yaml 的 proxies 必须是列表: {clash_path}")

    excluded = [str(value) for value in settings.get("exclude_name_patterns", [])]
    usa_nodes = _matching_nodes(
        proxies, [str(value) for value in settings.get("usa_name_patterns", ["美国"])], excluded
    )
    hongkong_nodes = _matching_nodes(
        proxies, [str(value) for value in settings.get("hongkong_name_patterns", ["香港"])], excluded
    )
    if not usa_nodes:
        raise RegionalProxyError("clash.yaml 中没有匹配到美国节点")
    if not hongkong_nodes:
        raise RegionalProxyError("clash.yaml 中没有匹配到香港节点")

    interval = int(settings.get("health_check_interval_seconds", 300))
    health_url = str(settings.get("health_check_url", "https://www.gstatic.com/generate_204"))

    def group(name: str, nodes: list[str]) -> dict:
        if len(nodes) == 1:
            return {"name": name, "type": "select", "proxies": nodes}
        return {
            "name": name,
            "type": "url-test",
            "url": health_url,
            "interval": interval,
            "tolerance": 50,
            "proxies": nodes,
        }

    return {
        "mixed-port": int(settings.get("mixed_port", 17891)),
        "allow-lan": False,
        "bind-address": "127.0.0.1",
        "mode": "rule",
        "log-level": str(settings.get("log_level", "warning")),
        "ipv6": False,
        "proxies": proxies,
        "proxy-groups": [group("USA-AUTO", usa_nodes), group("HK-AUTO", hongkong_nodes)],
        "rules": [
            "DOMAIN-SUFFIX,census.gov,USA-AUTO",
            "DOMAIN-SUFFIX,censtatd.gov.hk,HK-AUTO",
            "MATCH,DIRECT",
        ],
    }


def _find_mihomo(settings: dict) -> Path:
    configured = str(settings.get("mihomo_path", "")).strip()
    candidates = [Path(configured)] if configured else []
    for name in ("mihomo", "clash-meta", "clash"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    if __import__("os").name == "nt":
        candidates.extend(
            [
                Path(r"C:\Program Files\Clash Verge\verge-mihomo.exe"),
                Path(r"C:\Program Files\Clash Verge\verge-mihomo-alpha.exe"),
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RegionalProxyError(
        "未找到 Mihomo/Clash 内核；请在 config.yaml 的 regional_proxy.mihomo_path 指定可执行文件"
    )


class RegionalProxy:
    def __init__(self, config_path: Path = DEFAULT_CONFIG):
        config = _load_yaml(config_path)
        self.settings = config.get("regional_proxy") or {}
        self.enabled = bool(self.settings.get("enabled", False))
        self.port = int(self.settings.get("mixed_port", 17891))
        self.process: subprocess.Popen | None = None
        self.temp_dir: tempfile.TemporaryDirectory | None = None

    @property
    def proxy_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.enabled else None

    def start(self) -> "RegionalProxy":
        if not self.enabled or (self.process and self.process.poll() is None):
            return self
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                raise RegionalProxyError(
                    f"项目代理端口 {self.port} 已被占用，请修改 config.yaml 的 "
                    "regional_proxy.mixed_port"
                )
        except ConnectionRefusedError:
            pass
        except TimeoutError:
            pass
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (10061, 10060):
                raise
        runtime = build_runtime_config(self.settings, HERE)
        executable = _find_mihomo(self.settings)
        # Windows 上 mihomo 退出后释放 cache.db 句柄有延迟，清理失败只留垃圾不报错
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="automated-data-mihomo-", ignore_cleanup_errors=True
        )
        runtime_path = Path(self.temp_dir.name) / "config.yaml"
        runtime_path.write_text(
            yaml.safe_dump(runtime, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        creationflags = subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0
        self.process = subprocess.Popen(
            [str(executable), "-d", self.temp_dir.name, "-f", str(runtime_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + float(self.settings.get("startup_timeout_seconds", 20))
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                code = self.process.returncode
                self.stop()
                raise RegionalProxyError(f"Mihomo 启动失败，退出码 {code}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.3):
                    print(f"Regional proxy: {self.proxy_url} (USA/Hong Kong auto routing)")
                    return self
            except OSError:
                time.sleep(0.2)
        self.stop()
        raise RegionalProxyError(f"Mihomo 启动超时，本地端口 {self.port} 未就绪")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None
        if self.temp_dir:
            try:
                self.temp_dir.cleanup()
            except OSError:
                pass  # mihomo 句柄未释放时留给系统临时目录清理
            self.temp_dir = None


_instance: RegionalProxy | None = None
_lock = threading.Lock()


def get_proxy() -> RegionalProxy:
    global _instance
    with _lock:
        if _instance is None:
            _instance = RegionalProxy()
            atexit.register(_instance.stop)
        return _instance.start()


def configure_session(session) -> None:
    proxy_url = get_proxy().proxy_url
    if proxy_url:
        session.trust_env = False
        session.proxies.update({"http": proxy_url, "https": proxy_url})
