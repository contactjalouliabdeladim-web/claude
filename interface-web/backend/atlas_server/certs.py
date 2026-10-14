"""Certificats HTTPS locaux pour l'accès depuis le Mac (le micro exige HTTPS hors localhost).

* Une autorité locale « Atlas CA » (10 ans) est créée une seule fois. C'est ce
  certificat (``atlas-ca.crt``, public) qu'il faut marquer « de confiance » sur
  chaque appareil.
* Un certificat serveur (800 jours, limite acceptée par macOS) est signé par
  cette autorité pour : localhost, 127.0.0.1, le nom du PC et ses adresses IP
  locales. Il est régénéré automatiquement si l'adresse IP change, sans avoir
  à refaire la confiance sur le Mac.
* La clé privée de l'autorité ne quitte jamais le dossier ``donnees/certificats``.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def local_ip_addresses() -> list[str]:
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # adresse de la route par défaut (aucun paquet n'est envoyé)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254."))


def _write_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def _load_or_create_ca(cert_dir: Path):
    key_path, crt_path = cert_dir / "atlas-ca.key", cert_dir / "atlas-ca.crt"
    if key_path.exists() and crt_path.exists():
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        return key, x509.load_pem_x509_certificate(crt_path.read_bytes())
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Atlas CA ({socket.gethostname()})"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Atlas local")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    cert_dir.mkdir(parents=True, exist_ok=True)
    _write_key(key_path, key)
    crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def _server_names() -> tuple[list[str], list[str]]:
    host = socket.gethostname()
    dns = sorted({"localhost", host, f"{host}.local"})
    ips = ["127.0.0.1"] + local_ip_addresses()
    return dns, ips


def _cert_covers(cert_path: Path, dns: list[str], ips: list[str]) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except Exception:
        return False
    have_ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    have_dns = set(san.get_values_for_type(x509.DNSName))
    remaining = cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc)
    return set(ips) <= have_ips and set(dns) <= have_dns and remaining > dt.timedelta(days=30)


def ensure_server_certificate(cert_dir: Path) -> tuple[Path, Path, bool]:
    """Retourne (certificat, clé, régénéré)."""
    ca_key, ca_cert = _load_or_create_ca(cert_dir)
    crt_path, key_path = cert_dir / "atlas-serveur.crt", cert_dir / "atlas-serveur.key"
    dns, ips = _server_names()
    if crt_path.exists() and key_path.exists() and _cert_covers(crt_path, dns, ips):
        return crt_path, key_path, False
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Atlas")]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=800))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, key_cert_sign=False,
                                         crl_sign=False, content_commitment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False),
                           critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_key(key_path, key)
    # Chaîne complète : certificat serveur puis autorité.
    crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM)
                         + ca_cert.public_bytes(serialization.Encoding.PEM))
    return crt_path, key_path, True
