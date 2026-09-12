# 供应链验收与第一轮修复（M7 后续）

2026-09-10。PLAN 的编号模块到 M7 为止，本轮继续关闭其供应链缺口，没有
虚构新的 M8。已完成官方基础镜像内容核对、完整镜像扫描、第一轮修复及运行
复验。**当前仍不发布**：未修复/待研判的高危和严重匹配项没有被隐藏。

## 第二轮：严重项核查与 Debian 13 升级

继续核查后，当前候选改用 Debian 13（trixie）/ Python 3.12.14。SQLite 已从
3.40.1 升至 3.46.1，zlib 从 1.2.13 升至 1.3.1；对应严重匹配从扫描中消失。
[SQLite 官方发行版记录](https://security-tracker.debian.org/tracker/CVE-2025-7458)
和 [zlib 记录](https://security-tracker.debian.org/tracker/CVE-2023-45853) 支持该修复判断。

最新原始扫描为 **3 CRITICAL、51 HIGH**，可用修复版本的高危/严重匹配仍为 0。
剩余严重匹配均来自 perl-base：

| 项目 | 当前镜像证据 | 处理 |
|---|---|---|
| CVE-2026-42496 | Archive::Tar 无法加载，库/应用目录未找到该模块 | 受影响模块未包含；不在原始扫描中静默过滤 |
| CVE-2026-8376 | Perl ptrsize/ivsize 均为 8，amd64 | 不满足上游描述的 32 位条件；保留审核记录 |
| CVE-2026-13221 | 当前 Perl 包仍被发行版标记受影响 | 保留待研判，没有仅因应用主要用 Python 就豁免 |

依据分别为 [Archive::Tar 公告](https://security-tracker.debian.org/tracker/CVE-2026-42496)、
[32 位 Perl 公告](https://security-tracker.debian.org/tracker/CVE-2026-8376)、
[正则匹配问题记录](https://security-tracker.debian.org/tracker/CVE-2026-13221)。
证据限定于所测镜像/组件，不适用于其他架构、额外安装模块或静态内嵌副本。

新镜像 9 项浏览器测试通过，并验证了新镜像读取旧备份、旧 Bookworm 候选读取
新备份的 sg0004 兼容性。原始扫描未使用 VEX/ignore 豁免，门禁仍不通过。
`critical-triage/` 保存逐项来源及 runtime 证据，`trixie-*` 保存新一轮结果。

构建时为 apt 增加 HTTPS、30 秒连接/数据超时、一次重试和更新出错即失败。
本机 direct 下载停滞后，使用已有 `http_proxy`/`https_proxy` 作为临时 Docker
构建参数恢复下载；未写入代理地址/凭据，最终镜像层也检查了这些值不存在。

## 内容来源与第一轮构建改变

旧镜像缓存的 manifest digest 在官方仓库返回 404，但其 config blob 在
`registry-1.docker.io/library/python` 中可取得。下载内容 SHA-256 与本地配置
ID 相同，rootfs diff IDs 也与本地层一致，且为旧候选镜像的层前缀。因此旧
基础镜像的内容已核对为官方 Python 仓库内容，不能把 404 解释成镜像遭到篡改。
这项内容核对不等同于发布者数字签名验证。

基础镜像采用官方 Python **3.12.14 / linux-amd64**，摘要固定在
[base-image.lock.json](../deploy/shopguide/base-image.lock.json)。Dockerfile 默认
使用官方平台 manifest digest，不再使用浮动的 Python 3.11 标签。

本机 Docker daemon 拉取受限，因此增加 [导入脚本](../scripts/shopguide/import_python_base.py)：
从官方 Registry API 读取已锁定的 index、平台 manifest、config 和 layers，
逐一校验压缩内容 SHA、尺寸和解压后的 diff ID，最后导入 Docker 并重新核对。
不会把拉取 token 写入文件；跨域重定向时移除 Authorization。层作为完整 tar
传给 Docker，不在宿主机按镜像内路径解包。

Dockerfile 改为两阶段构建：构建阶段使用 uv 0.11.15，运行阶段只接收已安装
的应用环境与前端。运行镜像删除全局 pip/setuptools，不携带 uv/uvx；对操作
系统包执行签名发行源的安全更新。保持用户 10001、只读根文件系统和全部
capabilities 丢弃。uv.lock 继续约束应用依赖。

apt 安全仓库会随时间更新，因此未来重建需要重新扫描、记录最终镜像 ID 与
SBOM，不能仅凭相同 Dockerfile 宣称字节级可复现。仍未建立镜像发布签名流程。

## 扫描结果与边界（保留第一轮对照）

使用 Trivy **0.74.0**，二进制与压缩包均按官方发布校验和检查；版本、URL 和
哈希固定在 [scanner.lock.json](../deploy/shopguide/scanner.lock.json)。这不是
独立的发布者签名验证。漏洞库下载时间/更新时间随报告保存。

| 扫描匹配项 | 原 M7 | Bookworm 第一轮 | Trixie 当前 |
|---|---:|---:|---:|
| CRITICAL | 9 | 5 | 3 |
| HIGH | 90 | 55 | 51 |
| MEDIUM | 158 | 97 | 57 |
| LOW | 145 | 102 | 57 |
| UNKNOWN | 6 | 0 | 5 |
| 有修复版本的 HIGH/CRITICAL | 39 | 0 | 0 |

这里统计组件与漏洞的匹配项，同一 CVE 在多个包里会重复出现，不能把它称为
不同漏洞的数量。没有使用 ignore-unfixed、忽略列表或 VEX 豁免使扫描变绿。
第一轮剩余 60 个、当前剩余 54 个 HIGH/CRITICAL 匹配须逐项评估发行版状态、触发条件和修复路线；
没有现成修复版本不代表安全，也不能仅凭严重度就断言应用可被利用。

M7 之前的 pip-audit 主要检查应用虚拟环境。此次扫描覆盖了镜像的 OS 包、
全局 pip 和 uv 的可识别内嵌依赖，因此发现了先前范围之外的问题，不与之前
“应用依赖扫描为零”矛盾。修复后 npm audit 仍为 0。

第一轮 Bookworm 镜像检测到 105 个 OS 包，并生成包含扫描器识别到的 OS/语言包的 CycloneDX
SBOM。扫描器无法保证识别每一份静态链接或嵌入的 C/C++/Rust 代码，不能据此
声称所有原生库均已审完。参考 [Trivy 容器扫描说明](https://trivy.dev/docs/latest/target/container_image/)。

## 验证与剩余发布门禁

- 新增导入/扫描门禁/安装器测试 9 项；全量 Python **193 passed**。
- 新镜像的 Chromium/Playwright **9 passed**，包括型号切换竞态回归。
- 确认运行时找不到 uv、uvx、pip、pip3，应用可正常启动。
- 从新镜像创建冷备份，恢复至新目录；分别用新镜像和前一版本地镜像启动，
  检查原会话与原图。该回退范围是 sg0004、同应用协议的两个候选镜像，
  不代表破坏性 schema 降级或任意历史版本均受支持。
- 原候选镜像 `shopguide:m7-local` 保留作对照，新候选使用
  `shopguide:supply-chain-candidate`，Compose 已指向新名称。

基础内容对应关系和 OS 扫描这两项“尚未验证”已完成验证，但扫描未通过。
发布者签名、剩余漏洞处置、完整原生库覆盖，以及正式评测、生产认证等其他
门槛仍未满足。此轮不把它们标记为通过，也没有新增付费模型请求。

## 复现

从项目根目录执行。联网的 Docker daemon 可直接构建已固定摘要的基础镜像：

```bash
npm --prefix web ci
npm --prefix web run build
docker build -f deploy/shopguide/Dockerfile -t shopguide:supply-chain-candidate .
```

如本机 daemon 拉取受限，可先按锁定内容导入；目录必须不存在：

```bash
.venv-shopguide/bin/python scripts/shopguide/import_python_base.py \
  --index-digest sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea \
  --out artifacts/supply-chain/base-import --load
DOCKER_BUILDKIT=0 docker build --network host \
  --build-arg http_proxy --build-arg https_proxy \
  --build-arg PYTHON_IMAGE=shopguide-base:verified-2fe5997d249a808b \
  -f deploy/shopguide/Dockerfile -t shopguide:supply-chain-candidate .
```

扫描与门禁（会如实返回失败，不因暂无修复而放行）：

```bash
python scripts/shopguide/install_scanner.py \
  --lock deploy/shopguide/scanner.lock.json --out .cache/audit-scanner
mkdir -p artifacts/supply-chain
.cache/audit-scanner/trivy image --image-src docker --scanners vuln \
  --format json --output artifacts/supply-chain/scan.json shopguide:supply-chain-candidate
# 用 docker image inspect 查出的完整 image ID 替换下面的占位符。
python scripts/shopguide/summarize_image_scan.py \
  --scan artifacts/supply-chain/scan.json --image-id <完整image-ID> \
  --out artifacts/supply-chain/gate.json
```

人工触发的 [CI 工作流](../.github/workflows/supply-chain.yml) 已加入同样的镜像
扫描和门禁，失败时也保留产物；本轮没有触发远端 CI，没有推送或公开发布镜像。

本地产物在 `artifacts/shopguide/supply-chain/`：原/新 scan 与 summary、
`remaining-high-critical.json`、官方内容 proof、漏洞库 metadata、runtime SBOM、
浏览器/Python 测试和恢复/回退记录。旧失败结果均保留供对照。
