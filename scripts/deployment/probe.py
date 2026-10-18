"""Inspect actual container configuration without renewing or modifying authorization."""

from __future__ import annotations

import json
from typing import Any

from .common import DeploymentError, compose

# Only public configuration, fingerprints and error classes cross the process boundary.
_CONTAINER_PROBE = r"""
import hashlib,json,os,ssl,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from cryptography.hazmat.primitives import serialization
result={"configuration":{},"fingerprints":{},"health_ok":False,"route_ok":False}
fields=(
 "MAITU_LICENSE_CENTER_URL","MAITU_LICENSE_TRUSTED_KEYS_FILE","MAITU_LICENSE_STATE_DIR",
 "MAITU_UPDATE_CENTER_URL","MAITU_UPDATE_TRUSTED_KEYS_FILE","MAITU_UPDATE_QUEUE_DIR"
)
for field in fields:
 result["configuration"][field]=os.environ.get(field,"")
def fingerprints(path,domain):
 keys=json.loads(Path(path).read_text(encoding="utf-8"))
 if not isinstance(keys,dict) or not keys: raise ValueError("invalid_public_key_map")
 output={}
 for kid,pem in keys.items():
  key=serialization.load_pem_public_key(pem.encode("ascii"))
  der=key.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
  output[domain+":"+kid]=hashlib.sha256(der).hexdigest()
 return output
try:
 for field,domain in (("MAITU_LICENSE_TRUSTED_KEYS_FILE","license"),("MAITU_UPDATE_TRUSTED_KEYS_FILE","release")):
  result["fingerprints"].update(fingerprints(result["configuration"][field],domain))
 center=result["configuration"]["MAITU_LICENSE_CENTER_URL"]
 ca=os.environ.get("SSL_CERT_FILE")
 context=ssl.create_default_context(cafile=ca if ca and Path(ca).is_file() else None)
 opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),urllib.request.HTTPHandler(),)
 if not center: raise ValueError("configuration_required")
 with opener.open(center.rstrip("/")+"/health",timeout=8) as response:
  raw=response.read(65537)
  if len(raw)>65536: raise ValueError("oversized_health_response")
  health=json.loads(raw)
 result["health_ok"]=isinstance(health,dict) and health.get("status")=="ok"
 request=urllib.request.Request(center.rstrip("/")+"/api/v1/instance/challenges",method="OPTIONS")
 try:
  with opener.open(request,timeout=8) as response: result["route_status"]=response.status
 except urllib.error.HTTPError as exc: result["route_status"]=exc.code
 result["route_ok"]=result.get("route_status") in (200,204,405)
except Exception as exc:
 result["error_class"]=type(exc).__name__
url=result["configuration"].get("MAITU_LICENSE_CENTER_URL","")
try:
 parts=urllib.parse.urlsplit(url)
 if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
  result["configuration"]["MAITU_LICENSE_CENTER_URL"]="<invalid/redacted>"
except ValueError:
 result["configuration"]["MAITU_LICENSE_CENTER_URL"]="<invalid/redacted>"
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
