#!/usr/bin/env python3
"""Local Ubuntu deployment entry, intentionally runnable with system Python only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# deploy.sh uses isolated Python; import only this checked-out deployment package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deployment.common import DeploymentError, ask, deployment_lock, require_host  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="麦途双端部署：授权中心／业务平台终端向导及只读检查。不会远程 SSH、拉取最新代码或删除业务数据。"
    )
    parser.add_argument("command", nargs="?", choices=("center", "platform", "check"))
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("/etc/maitu-deploy"),
        help="持久部署记录目录（默认 /etc/maitu-deploy）",
    )
    parser.add_argument("--role", choices=("center", "platform"), help="check 时只检查指定角色")
    options = parser.parse_args(argv)
    try:
        command = options.command
        if command is None:
            print("1. 部署／配置授权管理中心\n2. 部署／配置业务平台\n3. 检查已有部署和对接状态")
            selected = ask("请选择 1、2 或 3")
            command = {"1": "center", "2": "platform", "3": "check"}.get(selected)
            if command is None:
                raise DeploymentError("请选择有效菜单项。")
        if options.role and command != "check":
            raise DeploymentError("--role 只用于只读 check 命令。")
        config_dir = options.config_dir.expanduser().absolute()
        if command == "center":
            from deployment.center import deploy

            require_host()
            with deployment_lock(config_dir):
                deploy(config_dir)
        elif command == "platform":
            from deployment.platform import deploy

            require_host()
            with deployment_lock(config_dir):
                deploy(config_dir)
        else:
            from deployment.diagnostics import check

            return check(config_dir, options.role)
    except DeploymentError as exc:
        print(f"部署检查未通过：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("操作已取消，未删除任何业务数据。请重新运行 check 查看当前状态。", file=sys.stderr)
        return 130
    except PermissionError:
        print(
            "权限不足；请核对 sudo、配置目录及 Docker 权限，不要使用 chmod 777。", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
