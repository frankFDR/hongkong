# automated_data — 纺织物贸易数据自动更新

一键把可自动获取的数据源更新到官方已发布的最新月份,全部内容自包含在本目录:

```
automated_data/
├── data/       # 最终产物: 四张贸易 CSV + 香港港口吞吐量 CSV
├── output/     # 每次运行的暂存输出
├── cache/      # API 响应缓存(按月/按年)
├── backup/     # 每次覆盖 data/ 前的自动备份(按日期)
├── update_all.py       # 一键入口
├── fetch_mainland_china.py # 中国海关统计月报来源
├── fetch_usa.py        # USA 来源
├── fetch_hongkong.py   # Hongkong 来源
└── fetch_hongkong_port.py # 香港海运/河运货物吞吐量
```

目前统一覆盖 **Mainland China**、**USA**、**Hongkong** 和 **Vietnam** 四个贸易数据来源。

## 裸服务器部署（Ubuntu / CentOS，支持无头）

把整个 `automated_data/` 文件夹拷到服务器任意目录即可。除文件夹自身外，只依赖
四样东西：**Python ≥ 3.10**、**requirements.txt 里的包**、**Playwright 的
Chromium 浏览器**（含系统共享库）、**mihomo 单文件内核**（不是装 Clash 客户端，
程序会自己启动它并读取同目录 `clash.yaml`）。

**无头服务器可直接跑**：检测不到图形环境（无 DISPLAY）时，海关采集自动切换
Chromium 无头模式，无需 X/桌面/额外配置。

前提条件（安装前确认）：
- **境内服务器**（海关中文站/英文站需要国内直连；境外机器抓不了海关和越南）；
- 服务器能直连机场节点服务器和 IPRoyal 网关（不需要预先装/开任何代理软件）；
- 有 root 或 sudo（装依赖 + 运行时给海关 CDN 加 `ip route` 直连路由）。

> 注意：若从 Windows 拷贝，先删掉文件夹里的 `.venv/`（那是 Windows 的虚拟环境，
> Linux 上不能用，下面会重建）。

### Ubuntu / Debian 安装步骤

```bash
cd automated_data
rm -rf .venv

# 1. 系统包
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip curl gzip iproute2 ca-certificates

# 2. Python 虚拟环境 + 依赖包
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
#   （网络慢可加 -i https://pypi.tuna.tsinghua.edu.cn/simple）

# 3. Chromium 浏览器 + 系统共享库（--with-deps 自动 apt 装齐）
sudo .venv/bin/playwright install --with-deps chromium

# 4. mihomo 内核（单文件,美国/香港数据源用;约 11MB）
curl -fL https://github.com/MetaCubeX/mihomo/releases/download/v1.19.2/mihomo-linux-amd64-v1.19.2.gz | gunzip > mihomo
#   GitHub 不通时换镜像: curl -fL https://ghfast.top/https://github.com/MetaCubeX/mihomo/releases/download/v1.19.2/mihomo-linux-amd64-v1.19.2.gz | gunzip > mihomo
chmod +x mihomo && sudo mv mihomo /usr/local/bin/
mihomo -v        # 验证
```

### CentOS / RHEL / Alma / Rocky 安装步骤

> 以下按 9 CentOS Stream 9 / Alma 9 /
> Rocky 9 写（8 系把 `python3.12` 换成 `python3.11` 同理）。

```bash
cd automated_data
rm -rf .venv

# 1. 系统包（dnf; CentOS 7 用 yum + EPEL 装 python3.11）
sudo dnf install -y python3.12 python3.12-pip curl gzip iproute ca-certificates

# 2. Python 虚拟环境 + 依赖包
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Chromium 浏览器。先试自动装依赖:
sudo .venv/bin/playwright install --with-deps chromium
#   如果报 "unsupported distribution / 不支持自动装依赖"，改为手动装共享库后再装浏览器:
sudo dnf install -y nss nspr atk at-spi2-atk cups-libs libdrm libxkbcommon \
    libXcomposite libXdamage libXfixes libXrandr mesa-libgbm alsa-lib \
    pango cairo liberation-fonts
.venv/bin/playwright install chromium

# 4. mihomo 内核（与 Ubuntu 相同）
curl -fL https://github.com/MetaCubeX/mihomo/releases/download/v1.19.2/mihomo-linux-amd64-v1.19.2.gz | gunzip > mihomo
chmod +x mihomo && sudo mv mihomo /usr/local/bin/
mihomo -v
```

### 运行

```bash
sudo .venv/bin/python update_all.py --dry-run   # 冒烟:只写 output/,不动 data/
sudo .venv/bin/python update_all.py             # 正式:先备份 data/ 再增量更新
sudo .venv/bin/python update_all.py --refresh all   # 全量重抓(忽略一切缓存)
.venv/bin/python fetch_usa.py                   # 单跑某个来源(不需要 sudo)
```

`sudo` 只为海关直连路由（`ip route add`）；不给 sudo 也能跑，只是中文站主源可能
失败，由英文站兜底补齐。建议定时任务（cron）直接用 root 跑 `update_all.py`。

### 两种核心场景

**A. 增量更新（已有 2025 及以前全部数据，补 2026 新月份）——默认行为**。
只要 `data/` 五张 CSV 在，即使 `cache/` 为空也不会全量重抓：播种机制把既有
CSV 当作已验证真值，只抓缺失月份并向官方最新已发布月延伸，几分钟完成。

**B. 冷启动全量抓取（完全没有数据）**：`data/` 为空时默认档自动走全量，或显式
`--refresh all`。参考耗时：中国海关约 1–2 小时（逐月浏览器采集 137+ 个月并建立
cache/，其后增量只需数分钟）；美国/香港为 API 来源，全量数分钟；越南走住宅代理
下载各年度工作簿，取决于代理速度。



## 用法

Vietnam: `python fetch_vietnam.py` 会在单个脚本内完成 NSO/GSO 官方附件发现、
下载、解析、聚合和完整性验证，结果写入 `output/Vietnam.csv`。脚本依赖第三方库
`xlrd==2.0.1` 和 `PyYAML`，
不依赖本项目内的其他代码、清单或预处理数据。

**越南代理（重要）**：nso.gov.vn 屏蔽境外 IP，代理商 IPRoyal 又默认屏蔽 `*.gov.vn`。
`fetch_vietnam.py` 内置一个进程内本地转发线程，对 gov 域名「本地解析成真 IP、只把 IP
发给 IPRoyal」绕过屏蔽，从而用**越南住宅 IP 直取真站**，覆盖全部年份 + 网站新进数据。
代理账号/国家/会话全部写在同目录 **`config.yaml`**（不硬编码，改配置即可）。
运行前若用 Clash/翻墙，需保持开启（内嵌转发要靠它出国够到 IPRoyal）。

抓数据前会做**连通性预检**并按原因分级提示：
- 网关不通 → 检查网络/Clash；
- 认证失败 → 检查 config.yaml 的 username/password；
- 已连上但建不了出口 → **多半是 IPRoyal 流量/余额用尽**，去后台查账户。

默认只抓解析所需的 E01/E02（省越南流量）。常用参数：`--archive-all`（额外镜像每年
页面上全部 .xls E01-E07，完整存档 manifest）、`--no-preflight`（跳过预检）、
`--proxy http://host:port`（用外部代理覆盖内嵌越南转发）、`--config 路径`、`--offline`。

```bash
cd automated_data
python update_all.py            # 更新 data/ 下全部 CSV(先备份、先对比)
python update_all.py --dry-run  # 只写到 output/,不动 data/
```

美国和香港数据源默认启用项目内自动路由。程序读取同目录 `clash.yaml`，按节点名中的
“美国”/“香港”生成自动选择组，并自动启动 Clash Verge 自带的 Mihomo 内核；美国
`census.gov` 走美国组，香港 `censtatd.gov.hk` 走香港组，其余域名直连。项目使用独立的
`127.0.0.1:17891`，不接管系统代理，也不改变中国大陆和越南的网络逻辑。相关开关、
节点名匹配词、端口及可选的内核路径均在 `config.yaml` 的 `regional_proxy` 下配置。

`--refresh` 控制缓存策略（三档，默认 `data`）：

```bash
python update_all.py --refresh auto  # 用 cache/,近期/当年重抓(默认,同不加参数)
python update_all.py --refresh all   # 忽略一切缓存,全量重抓(怀疑缓存损坏时用)
python update_all.py --refresh data  # 以 data/ 现有 CSV 为缓存:已有月份一律不抓,只补缺月
```

`--refresh data` 适合在新机器上只有 `data/`、没有 `cache/` 的冷启动场景，可避免全量重抓；
该档不检查官方对已有月份的修订，要修订请用 `auto` 或 `all`。

已有海关官网下载文件时，可跳过网页发现：
`python update_all.py --dry-run --china-import-source 进口美元.csv --china-export-source 出口美元.csv`。
每个参数均可重复传入，以合并多个年份文件。

也可以单独跑某个来源:`python fetch_mainland_china.py --browser` /
`python fetch_usa.py` / `python fetch_hongkong.py` /
`python fetch_vietnam.py`。越南历史工作簿缓存在 `cache/vietnam/`；需要断网重算时
使用 `python fetch_vietnam.py --offline`。
若自动网络设置不可用，可通过
`python fetch_vietnam.py --proxy http://proxy.example:8080` 显式指定代理。
香港港口吞吐量可独立运行：`python fetch_hongkong_port.py`，也会由 `update_all.py`
统一更新，不会覆盖商品贸易数据。

模型侧要用数据时,把 `data/` 下的 CSV 复制/指向到 `HongKongModel/textile_data/`,
再运行那边的 `prepare_data.py` 重建 `textile_common_monthly.csv` 和 `datasets/`
(注意 prepare_data 把公共区间硬性截断在 2025-12,内地/越南数据补上后需同步修改)。

## 数据源与口径

除 `HongKong_Port_Throughput.csv` 保持原结构外，四张贸易表统一为五列：
`年月,年,月,进口额,出口额`，其中 `年月` 为六位 `YYYYMM`。

### Mainland China → `data/mainlandChina.csv`
- **来源优先级（2026-07 起，精度优先）**：① 中文站统计月报的浏览器采集（.xls
  全精度权威版）；② 仅当中文站失败或有月份抓不到时，用英文站 Monthly Bulletin
  表（4）兜底（纯 HTTP、无风控，但表格是千美元舍入的展示版，见下节）。
  `--live-fresh` 仍然是纯中文官网完整复现，不做英文站兜底。
- 英文兜底月份在 manifest 里标记 `source_type: english_bulletin`；下次中文站可达时
  collect_online 会自动重抓这些月份，用全精度 .xls 替换回来。英文站最早只有
  2018 年，且个别月份缺失（如 2024-09/10 无链接），这些缺口只能靠中文站/缓存。
- 中文站来源：海关总署官方统计月报导航页及月度详情页中的 Excel 附件。
- 口径：美元、当月值，逐月提取并汇总 HS 第 50–63 章的进口额和出口额。
- 原始附件及来源 URL 清单缓存在 `cache/mainland_china/`；断网重算使用
  `python fetch_mainland_china.py --offline`。
- 单月抓取失败会在首轮结束后重建浏览器再集中补抓两轮；仍无法补齐时不会覆盖
  `data/mainlandChina.csv`，但会把已取得的月份保留为 `output/mainlandChina.partial.csv`，
  并在错误信息中列出缺月及部分文件路径。
- 官网“数据导出”CSV 为 GB18030 编码的逐章长表；分别选择进口/出口和美元后，可用
  `--import-source 文件 --export-source 文件` 直接转换，参数可重复传入多个文件。
- 若普通访问遇到海关 JS 风控，使用 `--browser --headed` 让真实 Chrome 渲染页面。
- 「绕过 Clash TUN 的海关直连路由」已跨平台：检测物理网关并按 /24 加直连路由，
  Windows 用 `route`+UAC 弹窗，macOS 用 `netstat`/`route`+系统管理员授权弹窗，
  Linux 用 `ip route`+sudo（先试免密 `sudo -n`，失败再交互输密码）。
  三个平台都会自动排除 Clash TUN(198.18.*)、Tailscale(100.*) 及 utun/tun/wg 等虚拟网卡。

### Mainland China（英文站，兜底来源）→ 并入上节流程；独立运行时输出 `output/mainlandChina_{Import,Export}_<年>_english.csv`
- 来源：海关总署英文站 Monthly Bulletin 表（4）「Imports and Exports by HS Section
  and Division」（`english.customs.gov.cn/statics/report/monthly.html`，往年为
  `monthly<年份>.html`）。与中文类章总值表同一套海关数据，美元口径（表内千美元，
  脚本已 ×1000 换算），输出表头与 `data/mainlandChina_*.csv` 完全一致。
- **无知道创宇 412 挑战、无滑块验证码**，普通 HTTP 即可抓取，境内外线路均可访问
  （脚本默认绕过系统代理直连，失败再回退系统代理）。发布节奏：当月数据约次月
  下旬～第三周发布（2026-07 中旬时已发布至 2026-05）。
- `update_all.py` 与 `fetch_mainland_china.py` 把该来源作为**兜底**（中文站优先保
  全精度，中文站失败/缺月时才用它补齐），网络预检同序。
  独立运行：`python fetch_mainland_china_english.py [--year 2026] [--months 4 5]`；
  页面缓存在 `cache/mainland_china_english/`。
- 注：stats.customs.gov.cn 在线查询平台受瑞数动态防护 + 拖拽滑块验证码保护，
  不适合自动化；仅当需要 HS 4/8 位细分数据时再人工使用该平台。

### USA → `data/USA_Total.csv`
- 来源:U.S. Census Bureau International Trade API(`porths` 端点),API key 已硬编码。
- 口径:HS 第 50–63 章逐章取全国汇总行(`PORT=-`、`CTY_CODE=-`),按月加总。
  进口总值取 `GEN_VAL_MO`,出口取 `ALL_VAL_MO`,单位美元。
- 起始 2015-01;发布滞后约 2 个月(例:5 月数据在 7 月初发布)。

### Hongkong → `data/Honkong.csv`
- 来源:政府统计处「贸易统计互动数据发布服务」(IDDS) 官方 API
  `https://tradeidds.censtatd.gov.hk/api/<api_id>/get`(api_id 来自 data.gov.hk 公开文档,
  调用前需先 GET 一次首页建立 cookie 会话)。
- 口径(与原手工整理文件逐值核对一致):
  - 只取 SITC 65(纺织纱、织物及制成品),不含 84(服装);
  - 千港元 × 1000 × **0.13 固定汇率** 换算为美元;
  - 五个贸易种类:进口 / 港产品出口 / 转口 / 整体出口 / 贸易总额。
- 起始 2015-01;发布滞后约 2 个月(当月下旬发布上上月)。

### 香港港口吞吐量 → `data/HongKong_Port_Throughput.csv`
- 来源：政府统计处表 `410-55111A`（按月海运货物吞吐量）和
  `410-55112A`（按月河运货物吞吐量）的官方 API。
- 起始 2015-01，自动下载到官方已发布的最新月份。
- 字段为海运/河运 × 抵港/离港，单位为千公吨。
- 与 `fetch_hongkong.py` 的纺织品进出口金额是两套独立数据，不互相覆盖。

## 缓存与备份

- `cache/`:API 响应按月(USA)/按年(HK)缓存,重复运行只请求新月份;
  最近月份/年份总是重新请求以捕捉官方修订。想全量重抓就删掉对应缓存子目录。
- `backup/<日期>/`:每次覆盖 `data/` 前自动备份。
- 每次运行会对新旧文件重叠月份逐值对比,官方修订过的历史数据会逐条打印。

## 网络说明

仓库不启动、配置或绑定任何代理软件，也不在下载前运行独立网络预检。
USA 和 Hongkong 下载使用 Python 标准网络会话；Vietnam 默认使用系统网络设置，
也可通过 `--proxy` 显式指定 HTTP/HTTPS 代理。连接失败时由对应下载任务直接报错。
