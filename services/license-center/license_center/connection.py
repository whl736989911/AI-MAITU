from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FORMAT = "maitu-license-connection"
VERSION = 1
MAX_BUNDLE_BYTES = 256 * 1024
_CERTIFICATE_BLOCK = re.compile(
    rb"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\r\n]+?-----END CERTIFICATE-----"
)


def validate_server_url(server_url: str) -> str:
    """Validate and normalize an HTTPS root origin for a center."""
    if not isinstance(server_url, str) or not server_url or server_url != server_url.strip():
        raise ValueError("Server URL must be an HTTPS root origin")
    try:
        parsed = urlsplit(server_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Server URL must be an HTTPS root origin") from exc
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or parsed.netloc.endswith(":")
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or "?" in server_url
        or "#" in server_url
    ):
        raise ValueError(
            "Server URL must be an HTTPS root origin without credentials or URL components"
        )
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Server port must be between 1 and 65535")
    host = hostname.lower()
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ipaddress.AddressValueError as exc:
            raise ValueError("Server URL must contain a valid host") from exc
        host = f"[{host}]"
    else:
        try:
            host = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Server URL must contain a valid host") from exc
        labels = host.rstrip(".").split(".")
        if len(host) > 253 or any(
            not label
            or len(label) > 63
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
            for label in labels
        ):
            raise ValueError("Server URL must contain a valid host")
    return f"https://{host}" + (f":{port}" if port is not None else "")


def validate_trusted_keys(trusted_keys: dict[str, str]) -> dict[str, str]:
    if not isinstance(trusted_keys, dict) or not trusted_keys or len(trusted_keys) > 128:
        raise ValueError("Between 1 and 128 trusted Ed25519 public keys are required")
    normalized: dict[str, str] = {}
    for kid, pem in trusted_keys.items():
        if (
            not isinstance(kid, str)
            or not kid.strip()
            or len(kid) > 256
            or any(ord(char) < 32 for char in kid)
            or not isinstance(pem, str)
        ):
            raise ValueError("Trusted keys must map valid nonempty key IDs to PEM public keys")
        if "PRIVATE KEY" in pem:
            raise ValueError("Private key material is not allowed in a connection bundle")
        try:
            key = serialization.load_pem_public_key(pem.encode("ascii"))
        except (ValueError, UnicodeEncodeError, TypeError) as exc:
            raise ValueError(f"Invalid public key for key ID {kid!r}") from exc
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError(f"Trusted key {kid!r} is not Ed25519")
        normalized[kid] = key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("ascii")
    return normalized


def validate_ca_certificate_pem(certificate_pem: str | None) -> str | None:
    if certificate_pem is None:
        return None
    if not isinstance(certificate_pem, str) or not certificate_pem.strip():
        raise ValueError("CA certificate PEM must contain one or more CA certificates")
    encoded = certificate_pem.encode("ascii", errors="strict")
    blocks = list(_CERTIFICATE_BLOCK.finditer(encoded))
    normalized_input = re.sub(rb"\s+", b"", encoded)
    normalized_blocks = re.sub(rb"\s+", b"", b"".join(block.group(0) for block in blocks))
    if not blocks or normalized_blocks != normalized_input:
        raise ValueError("CA certificate PEM must contain only PEM certificate blocks")
    for block in blocks:
        try:
            cert = x509.load_pem_x509_certificate(block.group(0))
            constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        except (ValueError, x509.ExtensionNotFound) as exc:
            raise ValueError("Invalid CA certificate PEM") from exc
        if not constraints.ca:
            raise ValueError("Certificate is not a CA certificate")
    return certificate_pem


def validate_ca_file(path: Path) -> str:
    try:
        content = path.expanduser().read_bytes()
        certificate_pem = content.decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Cannot read CA certificate file: {path}") from exc
    validate_ca_certificate_pem(certificate_pem)
    return certificate_pem


def create_bundle(
    server_url: str,
    trusted_keys: dict[str, str],
    ca_certificate_pem: str | None = None,
) -> dict[str, object]:
    return {
        "format": FORMAT,
        "version": VERSION,
        "server_url": validate_server_url(server_url),
        "trusted_keys": validate_trusted_keys(trusted_keys),
        "ca_certificate_pem": validate_ca_certificate_pem(ca_certificate_pem),
    }


def encode_bundle(bundle: dict[str, object]) -> str:
    encoded = json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"
    if len(encoded.encode("utf-8")) > MAX_BUNDLE_BYTES:
        raise ValueError("Connection bundle exceeds 256 KiB")
    return encoded
