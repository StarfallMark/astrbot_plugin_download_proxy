"""Explicit proxies scoped to AstrBot's plugin management clients."""

import asyncio
import os
import shlex
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import httpx


_MISSING = object()


class _MarketplaceHttp:
    def __init__(self, module, proxy):
        self.module = module
        self.proxy = proxy

    def __getattr__(self, name):
        return getattr(self.module, name)

    def ClientSession(self, *args, **kwargs):
        kwargs.setdefault("proxy", self.proxy)
        kwargs.setdefault("trust_env", False)
        return self.module.ClientSession(*args, **kwargs)


class PluginDownloadRouting:
    def __init__(self, proxy, proxy_dependencies=True):
        parsed = urlsplit(proxy)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("插件专用代理需要填写 http:// 或 https:// 地址")
        self.proxy = proxy
        self.proxy_dependencies = proxy_dependencies
        self.patches = []

    def _patch(self, target, name, replacement):
        previous = vars(target).get(name, _MISSING)
        setattr(target, name, replacement)
        self.patches.append((target, name, previous, replacement))

    def install(self, updater_class, marketplace_module, pip_installer):
        if self.patches:
            return
        proxy = self.proxy
        original_download = updater_class._download_repository
        original_inspect = updater_class.inspect_repository

        def create_client(updater, timeout=30.0):
            return httpx.AsyncClient(
                proxy=proxy, trust_env=False, follow_redirects=True,
                timeout=timeout, verify=updater._httpx_verify,
            )

        async def download_repository(updater, target_path, repo_url, proxy=""):
            # The dedicated network proxy connects directly to GitHub; omit mirrors.
            return await original_download(updater, target_path, repo_url, proxy="")

        async def inspect_repository(updater, repo_url, proxy=""):
            return await original_inspect(updater, repo_url, proxy="")

        async def clone_repository(updater, repo_url, target_path):
            executable = shutil.which("git")
            if not executable:
                raise RuntimeError("安装此仓库需要 Git，但当前运行环境中未找到 git 命令。")
            target = Path(target_path)
            if target.exists():
                raise RuntimeError(f"Git clone target already exists: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment.update({
                "http_proxy": proxy, "https_proxy": proxy,
                "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy,
                "GIT_TERMINAL_PROMPT": "0",
            })
            process = await asyncio.create_subprocess_exec(
                executable, "clone", "--depth", "1", "--single-branch",
                "--no-tags", "--", repo_url, str(target),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=environment,
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    process.kill()
                await process.communicate()
                shutil.rmtree(target, ignore_errors=True)
                raise
            if process.returncode:
                shutil.rmtree(target, ignore_errors=True)
                detail = stderr.decode(errors="replace").strip()[-2000:]
                raise RuntimeError(f"Git clone failed: {detail}")
            shutil.rmtree(target / ".git", ignore_errors=True)

        try:
            self._patch(updater_class, "_create_httpx_client", create_client)
            self._patch(updater_class, "_download_repository", download_repository)
            self._patch(updater_class, "inspect_repository", inspect_repository)
            self._patch(updater_class, "_clone_repository", clone_repository)
            self._patch(marketplace_module, "aiohttp", _MarketplaceHttp(marketplace_module.aiohttp, proxy))
            if self.proxy_dependencies:
                arguments = pip_installer.pip_install_arg or ""
                tokens = shlex.split(arguments)
                if not any(token == "--proxy" or token.startswith("--proxy=") for token in tokens):
                    arguments = (arguments + " --proxy " + shlex.quote(proxy)).strip()
                    self._patch(pip_installer, "pip_install_arg", arguments)
        except BaseException:
            self.remove()
            raise

    def remove(self):
        for target, name, previous, replacement in reversed(self.patches):
            if getattr(target, name, _MISSING) is not replacement:
                continue
            if previous is _MISSING:
                delattr(target, name)
            else:
                setattr(target, name, previous)
        self.patches.clear()
