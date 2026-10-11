"""Read-only public runtime inspection of the independently packaged center."""

from __future__ import annotations

import json
from typing import Any

from .common import DeploymentError, compose

_SCRIPT = r"""
import hashlib,json,os,pathlib,sqlite3
from cryptography.hazmat.primitives import serialization
from license_center.storage import DB_PATH
result={"environment":{},"fingerprints":{}}
for name in ("LICENSE_CENTER_DATA","LICENSE_CENTER_TRUSTED_PROXY_IPS","LICENSE_CENTER_TLS_CERT","LICENSE_CENTER_TIMEZONE"):
 result["environment"][name]=os.environ.get(name,"")
try:
 cert=pathlib.Path(os.environ["LICENSE_CENTER_TLS_CERT"])
 result["certificate_sha256"]=hashlib.sha256(cert.read_bytes()).hexdigest()
 with sqlite3.connect(DB_PATH.resolve().as_uri()+"?mode=ro",uri=True) as conn:
  result["owners"]=conn.execute("SELECT count(*) FROM admin_users").fetchone()[0]
  for kid,pem in conn.execute("SELECT kid,public_pem FROM signing_keys"):
   key=serialization.load_pem_public_key(pem.encode())
   result["fingerprints"][kid]=hashlib.sha256(key.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
except Exception as exc:
 result["error_class"]=type(exc).__name__
print(json.dumps(result))
"""


def inspect_center(config: dict[str, Any]) -> dict[str, Any]:
    result = compose(
        config,
        "exec",
        "-T",
        "license-center",
        "/app/.venv/bin/python",
        "-c",
        _SCRIPT,
        capture=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise DeploymentError("授权中心运行容器不可读；没有创建新容器或修改中心身份。")
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise DeploymentError("中心容器只读探针输出无效；没有打印原始输出。") from exc
    if not isinstance(data, dict):
        raise DeploymentError("中心容器只读探针输出无效。")
    return data
