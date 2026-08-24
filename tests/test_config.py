from src.config import load_config


def test_defaults_when_env_file_absent(tmp_path, monkeypatch):
    for key in (
        "BOT_GROUP_JID", "ADMIN_JIDS", "DB_PATH", "SESSION_PATH", "DRY_RUN",
        "GRACE_PERIOD_SECONDS", "CAPTION_GRACE_PERIOD_SECONDS",
        "TIER1_HOURS", "TIER2_DAYS", "PRESCRIPTION_DAYS",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = load_config(tmp_path / "does-not-exist.env")

    assert cfg.dry_run is True
    assert cfg.db_path == "data/beerbot.db"
    assert cfg.admin_jids == frozenset()
    assert cfg.grace_period_seconds == 90
    assert cfg.caption_grace_period_seconds == 300


def test_reads_values_from_env_file(tmp_path, monkeypatch):
    for key in ("BOT_GROUP_JID", "ADMIN_JIDS", "DRY_RUN"):
        monkeypatch.delenv(key, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "BOT_GROUP_JID=123-456@g.us\n"
        "ADMIN_JIDS=a@s.whatsapp.net, b@s.whatsapp.net\n"
        "DRY_RUN=false\n"
    )

    cfg = load_config(env_file)

    assert cfg.group_jid == "123-456@g.us"
    assert cfg.admin_jids == frozenset({"a@s.whatsapp.net", "b@s.whatsapp.net"})
    assert cfg.dry_run is False
