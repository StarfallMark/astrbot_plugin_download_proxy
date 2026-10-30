import asyncio
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
import httpx

from routing import PluginDownloadRouting


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class BaseUpdater:
            _httpx_verify = True

            def _create_httpx_client(self, timeout=30.0):
                return httpx.AsyncClient(trust_env=False, timeout=timeout)

            async def _download_repository(self, target_path, repo_url, proxy=""):
                return {"target": target_path, "url": repo_url, "mirror": proxy}

        class PluginUpdater(BaseUpdater):
            async def inspect_repository(self, repo_url, proxy=""):
                return {"url": repo_url, "mirror": proxy}

            async def _clone_repository(self, repo_url, target_path):
                raise AssertionError("unpatched clone called")

        self.base = BaseUpdater
        self.updater = PluginUpdater
        self.marketplace = SimpleNamespace(aiohttp=aiohttp)
        self.pip = SimpleNamespace(pip_install_arg="--timeout 60")
        self.original_updater = dict(vars(PluginUpdater))
        self.original_base = dict(vars(BaseUpdater))
        self.routing = None
        self.servers = []
        self.writers = []

    async def asyncTearDown(self):
        if self.routing:
            self.routing.remove()
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        for server in self.servers:
            server.close()
            await server.wait_closed()

    async def endpoint(self, response_body):
        requests = []

        async def handle(reader, writer):
            self.writers.append(writer)
            request = await reader.readuntil(b"\r\n\r\n")
            requests.append(request.split(b"\r\n", 1)[0].decode())
            payload = response_body.encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                + str(len(payload)).encode() + b"\r\n\r\n" + payload
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.servers.append(server)
        port = server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}", requests

    def install(self, proxy, proxy_dependencies=True):
        self.routing = PluginDownloadRouting(proxy, proxy_dependencies)
        self.routing.install(self.updater, self.marketplace, self.pip)

    async def test_plugin_http_uses_proxy_and_supplier_stays_direct(self):
        proxy_url, proxy_requests = await self.endpoint("plugin proxy")
        supplier_url, supplier_requests = await self.endpoint("supplier direct")
        environment = dict(os.environ)
        self.install(proxy_url)
        async with self.updater()._create_httpx_client() as client:
            response = await client.get("http://plugins.invalid/metadata.yaml")
        async with self.base()._create_httpx_client() as client:
            supplier = await client.get(supplier_url + "/v1/models")
        self.assertEqual(response.text, "plugin proxy")
        self.assertEqual(supplier.text, "supplier direct")
        self.assertEqual(proxy_requests, ["GET http://plugins.invalid/metadata.yaml HTTP/1.1"])
        self.assertEqual(supplier_requests, ["GET /v1/models HTTP/1.1"])
        self.assertEqual(dict(os.environ), environment)
        self.assertEqual(dict(vars(self.base)), self.original_base)

    async def test_marketplace_proxy_does_not_change_other_aiohttp_sessions(self):
        proxy_url, proxy_requests = await self.endpoint("market proxy")
        supplier_url, supplier_requests = await self.endpoint("direct")
        self.install(proxy_url)
        async with self.marketplace.aiohttp.ClientSession() as client:
            async with client.get("http://market.invalid/plugins.json") as response:
                self.assertEqual(await response.text(), "market proxy")
        async with aiohttp.ClientSession(trust_env=False) as client:
            async with client.get(supplier_url + "/models") as response:
                self.assertEqual(await response.text(), "direct")
        self.assertEqual(proxy_requests, ["GET http://market.invalid/plugins.json HTTP/1.1"])
        self.assertEqual(supplier_requests, ["GET /models HTTP/1.1"])

    async def test_plugin_http_ignores_process_proxy_environment(self):
        proxy_url, proxy_requests = await self.endpoint("dedicated")
        self.install(proxy_url)
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": "*"}):
            async with self.updater()._create_httpx_client() as client:
                response = await client.get("http://plugins.invalid/download.zip")
        self.assertEqual(response.text, "dedicated")
        self.assertEqual(len(proxy_requests), 1)

    async def test_github_mirrors_are_bypassed(self):
        self.install("http://127.0.0.1:7890")
        url = "https://github.com/example/plugin"
        result = await self.updater().inspect_repository(url, proxy="https://mirror.invalid")
        self.assertEqual(result, {"url": url, "mirror": ""})
        result = await self.updater()._download_repository("target", url, proxy="https://mirror.invalid")
        self.assertEqual(result, {"target": "target", "url": url, "mirror": ""})

    async def test_git_proxy_is_scoped_to_child_and_clone_keeps_files(self):
        environment = dict(os.environ)
        self.install("http://127.0.0.1:7890")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plugin"

            async def spawn(*args, **kwargs):
                target.mkdir()
                (target / ".git").mkdir()
                (target / "main.py").write_text("# plugin\n")
                return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))

            with patch("routing.shutil.which", return_value="/fake/git"), \
                    patch("routing.asyncio.create_subprocess_exec", side_effect=spawn) as spawn_mock:
                await self.updater()._clone_repository("https://example.test/plugin.git", target)
            args = spawn_mock.call_args.args
            child_env = spawn_mock.call_args.kwargs["env"]
            self.assertEqual(args[0:4], ("/fake/git", "clone", "--depth", "1"))
            for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                self.assertEqual(child_env[key], "http://127.0.0.1:7890")
            self.assertEqual(child_env["GIT_TERMINAL_PROMPT"], "0")
            self.assertFalse((target / ".git").exists())
            self.assertTrue((target / "main.py").exists())
        self.assertEqual(dict(os.environ), environment)

    async def test_pip_arguments_restore_on_disable(self):
        self.install("http://127.0.0.1:7890")
        self.assertEqual(shlex.split(self.pip.pip_install_arg),
                         ["--timeout", "60", "--proxy", "http://127.0.0.1:7890"])
        self.routing.remove()
        self.assertEqual(self.pip.pip_install_arg, "--timeout 60")

    async def test_existing_pip_proxy_takes_precedence(self):
        for arguments in ("--proxy http://other:1234", "--proxy=http://other:1234"):
            with self.subTest(arguments=arguments):
                self.pip.pip_install_arg = arguments
                self.install("http://127.0.0.1:7890")
                self.assertEqual(self.pip.pip_install_arg, arguments)
                self.routing.remove()

    async def test_dependency_proxy_can_be_disabled(self):
        self.install("http://127.0.0.1:7890", proxy_dependencies=False)
        self.assertEqual(self.pip.pip_install_arg, "--timeout 60")

    async def test_remove_restores_methods_and_sessions(self):
        self.install("http://127.0.0.1:7890")
        self.routing.install(self.updater, self.marketplace, self.pip)
        self.routing.remove()
        self.routing.remove()
        self.assertEqual(dict(vars(self.updater)), self.original_updater)
        self.assertEqual(dict(vars(self.base)), self.original_base)
        self.assertIs(self.marketplace.aiohttp, aiohttp)

    async def test_remove_keeps_later_changes_owned_by_other_plugins(self):
        self.install("http://127.0.0.1:7890")
        replacement = lambda self: None
        self.updater._create_httpx_client = replacement
        self.routing.remove()
        self.assertIs(self.updater._create_httpx_client, replacement)

    async def test_incomplete_install_rolls_back(self):
        self.routing = PluginDownloadRouting("http://127.0.0.1:7890")
        with self.assertRaises(AttributeError):
            self.routing.install(self.updater, SimpleNamespace(), self.pip)
        self.assertEqual(dict(vars(self.updater)), self.original_updater)
        self.assertEqual(self.pip.pip_install_arg, "--timeout 60")

    async def test_invalid_proxy_is_rejected(self):
        for proxy in ("", "127.0.0.1:7890", "socks5://127.0.0.1:7890"):
            with self.subTest(proxy=proxy), self.assertRaises(ValueError):
                PluginDownloadRouting(proxy)


if __name__ == "__main__":
    unittest.main()
