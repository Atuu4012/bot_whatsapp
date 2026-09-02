# Déploiement WhatsApp : iPhone + Raspberry Pi 4

## Contexte

Le projet vise à connecter le bot au vrai groupe WhatsApp (§13.4/§14 du plan initial — "phase WhatsApp", encore bloquée faute de matériel). La question de départ était de faire tourner tout le projet (bot + BDD) sur un seul iPhone, sous forme d'app iOS.

Ce n'est pas faisable tel quel : le bot dépend de `neonize`, un binding Python de `whatsmeow` (Go) qui maintient une connexion réseau persistante en arrière-plan pour recevoir les messages en temps réel. Les apps iOS distribuées via l'App Store ne peuvent pas faire tourner un démon en arrière-plan indéfiniment — c'est une restriction structurelle d'iOS, pas un problème de code.

Une piste alternative « tout sur un seul appareil » a été explorée (téléphone Android + Termux + proot-distro Debian, pour obtenir un vrai environnement glibc compatible avec le wheel `neonize`), mais écartée au profit d'un retour à l'architecture à deux appareils déjà prévue par le plan initial (§3.1), avec du matériel précis :

- **iPhone** : garde le vrai WhatsApp, avec le numéro dédié du bot. C'est l'« appareil principal » qu'exige le protocole multi-appareils de WhatsApp — il n'exécute aucun code du projet, juste l'app WhatsApp normale.
- **Raspberry Pi 4** : héberge le bot Python 24/7, connecté comme « appareil lié ». Remplace le VPS/vieux téléphone Android que le plan initial envisageait.

Cette architecture est déjà celle pour laquelle tout le code existant (`src/gateway.py::NeonizeGateway`, `src/main.py`, `requirements.txt`) a été écrit — **aucun changement de code n'est nécessaire**. Raspberry Pi OS est un vrai Linux Debian (glibc), donc le wheel `neonize` (`manylinux2014_aarch64`) s'installe normalement, sans les incertitudes qu'aurait posées un déploiement sur téléphone Android via Termux.

Le seul travail de code qui reste (indépendant du choix du matériel) est le TODO déjà documenté dans `src/main.py` : le câblage des événements `neonize` réels vers `IncomingMessage`, qui doit être validé sur un groupe de test WhatsApp réel avant tout branchement en production (§13.4) — donc pas quelque chose à coder à l'aveugle maintenant.

## Ce qui reste à faire

Ajouter au dépôt un guide de déploiement concret pour le Raspberry Pi 4, pour qu'il n'y ait plus qu'à suivre les étapes une fois le matériel en main. Pas de nouveau code Python.

### Fichier à modifier

**`README.md`** — ajouter une section « Déploiement (Raspberry Pi 4) » après la section « Démarrage rapide » existante, couvrant :

1. **Rôle de l'iPhone** : une phrase rappelant qu'il ne fait tourner aucun code du projet — juste WhatsApp avec le numéro dédié, à configurer et stabiliser *avant* de relier le Pi (repris de §3.1 du plan initial).
2. **Installation sur le Pi** : Raspberry Pi OS (64 bits, nécessaire pour le wheel `aarch64`), `git clone`, `python3 -m venv`, `pip install -r requirements.txt`, copie de `.env.example` vers `.env`.
3. **Service systemd** : une unit `beerbot.service` (`ExecStart=... python3 -m src.main`, `Restart=always`, `WantedBy=multi-user.target`) — reprend le `systemd`/`Restart=always` déjà mentionné dans le plan initial (§3.3/§11), avec le contenu exact de l'unit.
4. **Sauvegardes** : une entrée crontab pointant vers `scripts/backup.sh` déjà présent dans le dépôt (`0 4 * * * /chemin/vers/beerbot/scripts/backup.sh`), avec une note sur `BEERBOT_BACKUP_DIR` à pointer vers un stockage externe/monté — la sauvegarde doit sortir physiquement du Pi pour être utile en cas de casse/perte.
5. **Rappel dry-run** : lien vers l'avertissement déjà présent dans le README (`DRY_RUN=true` pendant 2 semaines avant d'activer les sanctions réelles).
6. Note explicite que le TODO neonize (`src/main.py`) doit être complété et validé sur un groupe de test (§13.4) avant de brancher ce service sur le vrai groupe — ce guide de déploiement ne dépend pas de ce TODO mais ne remplace pas cette étape.

Pas d'autre fichier touché : `requirements.txt`, `src/`, `scripts/backup.sh`, `.env.example` restent inchangés — ils ciblent déjà cette architecture.

## Vérification

- Relecture du diff `README.md` pour cohérence avec le reste du fichier (ton, format des sections existantes).
- Pas de test automatisé applicable (changement purement documentaire) — la suite de tests existante (94 tests) n'est pas affectée.
