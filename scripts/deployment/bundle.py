"""Import the public issuer connection format without installing Python packages."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .common import DeploymentError, atomic_write, check_url, ensure_directory

FORMAT = "maitu-license-connection"
MAX_BYTES = 256 * 1024
FIELDS = {"format", "version", "server_url", "trusted_keys", "ca_certificate_pem"}
_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
_CERT = re.compile(r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----")


def _public_der(pem: str) -> bytes:
    if len(pem) > 8192 or "PRIVATE KEY" in pem or not pem.startswith("-----BEGIN PUBLIC KEY-----"):
        raise DeploymentError("接入包只能包含 Ed25519 公开验签密钥，不能包含私钥。")
    try:
        result = subprocess.run(
            ["openssl", "pkey", "-pubin", "-outform", "DER"],
            input=pem.encode("ascii"),
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise DeploymentError("无法检查公钥；请确认 OpenSSL 已安装并且公钥格式正确。") from exc
    if (
        result.returncode
        or len(result.stdout) != 44
        or not result.stdout.startswith(_ED25519_SPKI_PREFIX)
    ):
        raise DeploymentError("接入包包含无效或非 Ed25519 验签公钥。")
    return result.stdout


def _validate_ca(pem: str) -> None:
    if "PRIVATE KEY" in pem:
        raise DeploymentError("CA 配置只能包含公开根证书，不能包含私钥。")
    certificates = _CERT.findall(pem)
    if not certificates or _CERT.sub("", pem).strip():
        raise DeploymentError("CA 内容必须是完整 PEM 证书链。")
    for certificate in certificates:
        try:
            result = subprocess.run(
                ["openssl", "x509", "-noout", "-text"],
                input=certificate,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DeploymentError("无法检查 CA 证书，请确认 OpenSSL 已安装。") from exc
        if result.returncode or "CA:TRUE" not in result.stdout:
            raise DeploymentError("接入包 CA 必须是具有 CA 约束的有效公开证书。")


def validate_bundle(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise DeploymentError("接入包字段不完整或含未知字段；请从当前中心重新导出完整接入包。")
    if data["format"] != FORMAT or type(data["version"]) is not int or data["version"] != 1:
        raise DeploymentError("不兼容的接入包格式或版本。")
    if not isinstance(data["server_url"], str):
        raise DeploymentError("接入包中心地址必须是字符串。")
    normalized = check_url(data["server_url"])
    keys = data["trusted_keys"]
    if not isinstance(keys, dict) or not keys or len(keys) > 128:
        raise DeploymentError("接入包缺少可信公钥，或公钥数量超出限制。")
    for kid, pem in keys.items():
        if (
            not isinstance(kid, str)
            or not kid.strip()
            or len(kid) > 256
            or any(ord(c) < 32 for c in kid)
        ):
            raise DeploymentError("签名公钥 kid 无效。")
        if not isinstance(pem, str):
            raise DeploymentError("公开密钥必须是 PEM 字符串。")
        _public_der(pem)
    ca = data["ca_certificate_pem"]
    if ca is not None:
        if not isinstance(ca, str) or not ca:
            raise DeploymentError("CA 字段必须是 null 或非空 PEM 证书链。")
        _validate_ca(ca)
    if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise DeploymentError("接入包超过 256 KiB 限制。")
    return {**data, "server_url": normalized}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeploymentError("接入包含重复 JSON 字段，拒绝使用歧义配置。")
        result[key] = value
    return result


def load_bundle(path: Path) -> dict[str, Any]:
    try:
        with path.expanduser().open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise DeploymentError("无法读取接入包，请检查文件路径和权限。") from exc
    if len(raw) > MAX_BYTES:
        raise DeploymentError("接入包超过 256 KiB 限制。")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise DeploymentError("接入包不是有效的 UTF-8 JSON 文件。") from exc
    return validate_bundle(data)


def key_fingerprints(bundle: dict[str, Any]) -> dict[str, str]:
    return {
        kid: hashlib.sha256(_public_der(pem)).hexdigest()
        for kid, pem in bundle["trusted_keys"].items()
    }


def install_bundle(bundle: dict[str, Any], trust_dir: Path) -> tuple[Path, Path | None]:
    validated = validate_bundle(bundle)
    ensure_directory(trust_dir, mode=0o755)
    trust_path = trust_dir / "trusted-keys.json"
    ca_path = trust_dir / "root-ca.pem" if validated["ca_certificate_pem"] is not None else None
    atomic_write(trust_path, json.dumps(validated["trusted_keys"], indent=2) + "\n", mode=0o644)
    if ca_path is not None:
        atomic_write(ca_path, validated["ca_certificate_pem"], mode=0o644)
    # An old CA is not deleted: the effective configuration points to ca_path (or empty).
    return trust_path, ca_path
