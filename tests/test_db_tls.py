"""Database TLS verification settings (#203)."""
import ssl
from pathlib import Path

from backend.config import settings
from backend.database import db_connect_args

BUNDLE = Path(__file__).resolve().parent.parent / "backend" / "certs" / "rds-global-bundle.pem"


def test_no_root_cert_keeps_the_driver_default(monkeypatch):
    monkeypatch.setattr(settings, "DB_SSL_ROOT_CERT", "")
    assert db_connect_args() == {}


def test_root_cert_requires_a_verified_certificate_and_hostname(monkeypatch):
    monkeypatch.setattr(settings, "DB_SSL_ROOT_CERT", str(BUNDLE))
    ctx = db_connect_args()["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    # Loaded the bundle's CAs, not just the system store.
    assert len(ctx.get_ca_certs()) >= 100


def test_production_points_at_the_shipped_bundle():
    ecs = (Path(__file__).resolve().parent.parent / "terraform" / "ecs.tf").read_text()
    assert '"/app/backend/certs/rds-global-bundle.pem"' in ecs
    assert BUNDLE.read_text().count("BEGIN CERTIFICATE") >= 100
