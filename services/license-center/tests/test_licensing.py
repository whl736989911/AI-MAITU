from __future__ import annotations

import hashlib
import json

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from helpers import active_license_public_pem, customer_and_activation

from license_center import crypto, storage
from license_center.models import ProofRequest


def _public_pem(private_key):
    return (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )


def _proof(private_key, body, operation):
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    claims = {
        "protocol": 1,
        "purpose": "instance-proof",
        "product": "maitu",
        "aud": "maitu-license-center",
        "iss": body["instance_id"],
        "instance_id": body["instance_id"],
        "public_key": body["public_key_pem"],
        "operation": operation,
        "challenge": body["challenge"],
        "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
    if "activation_code" in body:
        claims["activation_code"] = body["activation_code"]
    return jwt.encode(
        claims,
        private_key,
        algorithm="EdDSA",
        headers={"kid": "maitu-license-1", "typ": "JWT"},
    )


def _activate(client, private_key, instance_id, code):
    public_pem = _public_pem(private_key)
    challenge = client.post(
        "/api/v1/instance/challenges",
        json={"instance_id": instance_id, "operation": "activate", "public_key_pem": public_pem},
    )
    assert challenge.status_code == 200, challenge.text
    body = {
        "instance_id": instance_id,
        "public_key_pem": public_pem,
        "challenge": challenge.json()["challenge"],
        "activation_code": code,
    }
    response = client.post(
        "/api/v1/instance/activate",
        json={**body, "proof": _proof(private_key, body, "activate")},
    )
    return response, body


def _sync(client, private_key, instance_id):
    challenge = client.post(
        "/api/v1/instance/challenges", json={"instance_id": instance_id, "operation": "sync"}
    )
    assert challenge.status_code == 200, challenge.text
    public_pem = _public_pem(private_key)
    body = {
        "instance_id": instance_id,
        "challenge": challenge.json()["challenge"],
        "public_key_pem": public_pem,
    }
    request = ProofRequest(
        instance_id=instance_id,
        public_key_pem=public_pem,
        challenge=body["challenge"],
        proof=jwt.encode(
            {
                "iss": instance_id,
                "purpose": "instance-proof",
                "protocol": 1,
                "product": "maitu",
                "aud": "maitu-license-center",
                "instance_id": instance_id,
                "public_key": _public_pem(private_key),
                "operation": "sync",
                "challenge": body["challenge"],
                "content_hash": hashlib.sha256(
                    json.dumps(
                        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                ).hexdigest(),
            },
            private_key,
            algorithm="EdDSA",
            headers={"kid": "maitu-license-1", "typ": "JWT"},
        ),
    )
    response = client.post("/api/v1/instance/sync", json=request.model_dump())
    return response, body


def _decode_token(token, public_pem, instance):
    claims = jwt.decode(
        token,
        serialization.load_pem_public_key(public_pem.encode()),
        algorithms=["EdDSA"],
        audience="maitu-instance",
        issuer="maitu-license-center",
        options={"verify_exp": False},
    )
    assert claims["instance_id"] == instance
    return claims


def _offline_request(private_key, instance_id):
    public_pem = _public_pem(private_key)
    fingerprint = crypto.fingerprint(public_pem)
    claims = {
        "protocol": 1,
        "product": "maitu",
        "aud": "maitu-license-center",
        "iss": instance_id,
        "purpose": "offline-request",
        "instance_id": instance_id,
        "key_fingerprint": fingerprint,
    }
    proof = jwt.encode(claims, private_key, algorithm="EdDSA")
    return {
        "instance_id": instance_id,
        "public_key_pem": public_pem,
        "key_fingerprint": fingerprint,
        "proof": proof,
    }


def test_http_activation_proof_binds_content_challenge_and_instance_key(center):
    client, _ = center
    _, activation = customer_and_activation(client, center[1])
    private_key = Ed25519PrivateKey.generate()
    instance_id = crypto.fingerprint(_public_pem(private_key))[:32]
    accepted, request = _activate(client, private_key, instance_id, activation["code"])
    assert accepted.status_code == 200, accepted.text
    signer_pem = active_license_public_pem(client)
    claims = _decode_token(accepted.json()["token"], signer_pem, instance_id)
    assert claims["purpose"] == "online-license"
    assert claims["license_version"] == 1
    assert claims["challenge"] == request["challenge"]

    changed = {
        "instance_id": instance_id,
        "public_key_pem": _public_pem(private_key),
        "challenge": request["challenge"],
        "activation_code": activation["code"],
    }
    tampered = client.post(
        "/api/v1/instance/activate",
        json={
            **changed,
            "activation_code": "different-code",
            "proof": _proof(private_key, request, "activate"),
        },
    )
    assert tampered.status_code == 401
    replayed = client.post(
        "/api/v1/instance/activate",
        json={**request, "proof": _proof(private_key, request, "activate")},
    )
    assert replayed.status_code == 409
    replay, _ = _activate(client, private_key, instance_id, activation["code"])
    assert replay.status_code == 200


def test_offline_issue_and_online_revocation_share_license_revision(center):
    client, csrf = center
    customer, activation = customer_and_activation(client, csrf)
    private_key = Ed25519PrivateKey.generate()
    instance_id = crypto.fingerprint(_public_pem(private_key))[:32]
    activated, _ = _activate(client, private_key, instance_id, activation["code"])
    assert activated.status_code == 200, activated.text
    signer_pem = active_license_public_pem(client)
    online = _decode_token(activated.json()["token"], signer_pem, instance_id)
    license_id = online["license_id"]
    offline_response = client.post(
        "/api/v1/admin/offline-licenses",
        headers=csrf,
        json={
            "customer_id": customer["id"],
            "request": _offline_request(private_key, instance_id),
            "license_expires_at": online["license_expires_at"],
            "offline_expires_at": storage.now() + 7 * 86400,
            "reason": "Offline installation",
        },
    )
    assert offline_response.status_code == 200, offline_response.text
    offline = _decode_token(offline_response.json()["token"], signer_pem, instance_id)
    assert offline["purpose"] == "offline-license"
    assert offline["license_id"] == license_id
    assert offline["license_version"] == online["license_version"] == 1
    assert offline_response.json()["license_version"] == 1

    revoked = client.post(
        f"/api/v1/admin/licenses/{license_id}/revoke",
        headers=csrf,
        json={"reason": "Customer request", "version": 1},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revision"] == 2
    synced, _ = _sync(client, private_key, instance_id)
    assert synced.status_code == 200, synced.text
    denial = _decode_token(synced.json()["token"], signer_pem, instance_id)
    assert denial["status"] == "revoked"
    assert denial["license_version"] == offline["license_version"] + 1
    assert denial["license_id"] == offline["license_id"]
    assert (
        client.post(
            f"/api/v1/admin/licenses/{license_id}/restore",
            headers=csrf,
            json={
                "reason": "Approved restore",
                "version": 2,
                "expires_at": online["license_expires_at"] + 86400,
            },
        ).json()["revision"]
        == 3
    )
