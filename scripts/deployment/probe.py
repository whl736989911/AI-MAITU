"""Inspect actual container configuration without renewing or modifying authorization."""

from __future__ import annotations

import json
from typing import Any

from .common import DeploymentError, compose

# Only public configuration, fingerprints and error classes cross the process boundary.
_CONTAINER_PROBE = r"""
import hashlib,json,os,ssl,urllib.request,urllib.error
from cryptography.hazmat.primitives import serialization
from octop.infra.utils.env_file import apply_env_file,env_file_path
from octop.infra.utils.paths import PathLayout
from octop.infra.licensing.service import LicenseService
home=PathLayout.from_env().root
apply_env_file(env_file_path(home))
result={"configuration":{},"fingerprints":{},"health_ok":False,"route_ok":False}
fields=("OCTOP_LICENSE_SERVER_URL","OCTOP_LICENSE_TRUST_FILE","OCTOP_LICENSE_CA_FILE")
for field in fields:
 result["configuration"][field]=os.environ.get(field,"")
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None
try:
 service=LicenseService(home)
 service.load_cached()
 result["disk_status"]=service.status()
 for kid,key in service.trusted_keys.items():
  der=key.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
  result["fingerprints"][kid]=hashlib.sha256(der).hexdigest()
 context=ssl.create_default_context(cafile=service.ca_file) if service.ca_file else ssl.create_default_context()
 opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),NoRedirect())
 if not service.server_url: raise ValueError("configuration_required")
 with opener.open(service.server_url+"/health",timeout=8) as response:
  raw=response.read(65537)
  if len(raw)>65536: raise ValueError("oversized_health_response")
  health=json.loads(raw)
 result["health_ok"]=isinstance(health,dict) and health.get("status")=="ok"
 request=urllib.request.Request(service.server_url+"/api/v1/instance/challenges",method="OPTIONS")
 try:
  with opener.open(request,timeout=8) as response:
   result["route_status"]=response.status
 except urllib.error.HTTPError as exc:
  result["route_status"]=exc.code
 result["route_ok"]=result.get("route_status") in (200,204,405)
except Exception as exc:
 result["error_class"]=type(exc).__name__
# Avoid showing a malformed secret-bearing URL from an operator environment override.
url=result["configuration"].get("OCTOP_LICENSE_SERVER_URL","")
try:
 parts=urllib.parse.urlsplit(url)
 if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
  result["configuration"]["OCTOP_LICENSE_SERVER_URL"]="<invalid/redacted>"
except ValueError:
 result["configuration"]["OCTOP_LICENSE_SERVER_URL"]="<invalid/redacted>"
print(json.dumps(result))
"""


def inspect_platform(config: dict[str, Any]) -> dict[str, Any]:
    result = compose(
        config,
        "exec",
        "-T",
        "maitu",
        "python",
        "-c",
        _CONTAINER_PROBE,
        capture=True,
        check=False,
        timeout=40,
    )
    if result.returncode:
        raise DeploymentError("无法从实际运行容器读取对接配置；检查未启动新容器或修改授权。")
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise DeploymentError("容器诊断输出格式无效；未打印敏感原始输出。") from exc
    if not isinstance(data, dict):
        raise DeploymentError("容器诊断输出格式无效。")
    return data
