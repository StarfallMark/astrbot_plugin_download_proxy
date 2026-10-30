# 插件下载专用代理

只给 AstrBot 的插件市场、插件下载安装和更新使用代理，供应商继续使用各自的网络配置。

## 安装与设置

在 AstrBot 插件管理中通过仓库地址安装：

```text
https://github.com/StarfallMark/astrbot_plugin_download_proxy
```

首次安装时可下载本仓库 ZIP，在插件管理中选择“从文件安装”。

在插件配置中设置代理，默认 `http://127.0.0.1:7890`，并将 AstrBot 的“网络 → HTTP 代理”留空。
如果 AstrBot 运行在 Docker 中，需要填写容器能访问到的代理地址。

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `enable` | `true` | 启用插件专用代理 |
| `proxy` | `http://127.0.0.1:7890` | HTTP 或 HTTPS 代理地址 |
| `proxy_dependencies` | `true` | Python 依赖安装也使用代理；已有的 pip `--proxy` 参数优先 |

插件设置修改后由 AstrBot 重载生效。

## 代理范围

覆盖插件市场及其索引校验、GitHub 元数据、插件 ZIP 下载更新、HTTP(S) Git
克隆和 pip 依赖安装。启用时插件下载跳过 GitHub 加速镜像，直接通过代理访问
原始 GitHub 地址。此插件不配置插件运行期间自行发起的网络请求。

插件只替换插件管理器的网络客户端，以及 pip 安装参数，不设置 `http_proxy`、
`https_proxy` 等进程环境变量，也不修改 AstrBot 已安装的核心源码。禁用或卸载
时恢复由本插件替换的客户端和参数。Git 代理环境变量只传给插件克隆的子进程。

## 兼容性与验证

已在 AstrBot 4.28.2、HTTPX 0.28.1、aiohttp 3.14.3 上验证。
插件使用 AstrBot 插件管理器的内部接口，其他版本需要核对接口兼容性。
HTTPX 需支持 `AsyncClient(proxy=...)`，aiohttp 需支持 `ClientSession(proxy=...)`（3.12 及以上）。

运行测试：

```bash
python -m unittest discover -s tests -v
```

测试覆盖实际本地 HTTP 代理转发、供应商客户端隔离、Git 子进程环境、镜像绕过、pip 参数恢复和加载失败回滚。

本项目使用 MIT 许可证。
