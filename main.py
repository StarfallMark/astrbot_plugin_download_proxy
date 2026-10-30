from astrbot.api import logger
from astrbot.api.star import Context, Star, register

from .routing import PluginDownloadRouting


@register("astrbot_plugin_download_proxy", "StarfallMark", "只为插件市场、下载更新和依赖安装使用独立代理", "1.0.0")
class PluginDownloadProxy(Star):
    def __init__(self, context: Context, config):
        super().__init__(context, config)
        self.config = config
        self.routing = None

    async def initialize(self):
        if not self.config.get("enable", True):
            return
        from astrbot.core import pip_installer
        from astrbot.core.star.updater import _PluginUpdater
        from astrbot.dashboard.services import plugin_service

        self.routing = PluginDownloadRouting(
            self.config.get("proxy", "http://127.0.0.1:7890"),
            self.config.get("proxy_dependencies", True),
        )
        self.routing.install(_PluginUpdater, plugin_service, pip_installer)
        logger.info("插件专用代理已启用：插件市场、下载更新和依赖安装；未设置全局环境代理。")

    async def terminate(self):
        if self.routing:
            self.routing.remove()
            self.routing = None
