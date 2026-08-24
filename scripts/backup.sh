#!/usr/bin/env bash
# Sauvegarde quotidienne de la base et de la session WhatsApp (§11 du plan).
# À lancer via cron, par exemple : 0 4 * * * /chemin/vers/beerbot/scripts/backup.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BEERBOT_BACKUP_DIR:-$PROJECT_DIR/backups}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

if [ -f "$PROJECT_DIR/data/beerbot.db" ]; then
    cp "$PROJECT_DIR/data/beerbot.db" "$BACKUP_DIR/beerbot-$TIMESTAMP.db"
fi

if [ -f "$PROJECT_DIR/data/session.db" ]; then
    cp "$PROJECT_DIR/data/session.db" "$BACKUP_DIR/session-$TIMESTAMP.db"
fi

# Garde 30 jours d'historique, supprime le reste.
find "$BACKUP_DIR" -name 'beerbot-*.db' -mtime +30 -delete
find "$BACKUP_DIR" -name 'session-*.db' -mtime +30 -delete

echo "Backup terminé : $BACKUP_DIR/beerbot-$TIMESTAMP.db"
