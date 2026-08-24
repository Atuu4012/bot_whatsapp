# 🍺 BeerBot — compteur de bières WhatsApp

Bot de modération pour un groupe WhatsApp où chaque bière se poste en photo,
avec le numéro suivant en légende. Un message qui ne respecte pas la règle
(pas de photo, pas de légende, mauvais numéro) vaut un avertissement en DM
puis une expulsion du groupe, avec réintégration automatique après un délai
qui augmente à chaque récidive.

Le plan détaillé (architecture, choix techniques, risques) vit en local dans
`plan-bot-biere.md`, volontairement gardé hors du dépôt Git (voir *Vie
privée* plus bas).

## Les règles

| Fonction | Détail |
|---|---|
| **Valider** | Un message conforme = 1 photo + une légende qui ne contient que le(s) numéro(s) attendu(s) (compteur + 1, éventuellement plusieurs numéros consécutifs pour rattraper plusieurs bières d'un coup) |
| **Sanctionner** | Message non conforme → DM d'explication puis expulsion |
| **Compter** | Le compteur est toujours `MAX(number)` en base, jamais une variable en mémoire |
| **Célébrer** | Message automatique dans le groupe aux paliers (1000, 2500, 5000, tous les 500…) |
| **Statistiquer** | Classements, séries, récap hebdomadaire |

L'escalade des sanctions : 1ʳᵉ infraction → 24 h (retour auto), 2ᵉ → 7 jours
(retour auto), 3ᵉ → retour validé par un admin. Une infraction se prescrit
au bout de 90 jours sans récidive.

## État du projet

- ✅ **Phase locale** — tout ce qui se teste sans connexion WhatsApp : base de
  données, validateur, moteur de décision, modération, paliers, stats,
  parsing de l'export, import de l'historique. 67 tests, tous verts.
- ⏳ **Phase WhatsApp** — connexion réelle via [neonize](https://github.com/krypton-byte/neonize)
  (protocole whatsmeow). Bloquée sur la SIM dédiée et un groupe de test :
  voir `src/gateway.py` (`NeonizeGateway`) et le TODO dans `src/main.py`.
  Ne rien brancher sur le vrai groupe avant d'avoir fait tourner le bot
  2 semaines en `DRY_RUN=true`.

## Démarrage rapide

```bash
python -m venv .venv
.venv/Scripts/activate        # ou source .venv/bin/activate sous Unix
pip install -r requirements.txt
cp .env.example .env          # à compléter avant de lancer main.py
```

Lancer les tests :

```bash
python -m pytest
```

Importer l'historique WhatsApp (export du groupe → `Exporter la discussion`) :

```bash
python scripts/link_members.py export.txt membres.csv   # génère un CSV nom → jid à compléter à la main
python scripts/import_history.py export.txt membres.csv data/beerbot.db
```

Vérifier les règles de validation contre l'historique réel avant toute mise
en prod (aucune écriture en base, aucune connexion WhatsApp) :

```bash
python scripts/replay.py export.txt
```

## Structure

```
src/
├── main.py        # câblage des dépendances, point d'entrée
├── config.py       # chargement du .env
├── gateway.py      # abstraction du protocole WhatsApp (Protocol + NeonizeGateway)
├── engine.py       # reçoit un message, décide, agit
├── db.py           # schéma SQLite + accès aux données
├── validator.py     # conformité d'un message (photo + légende)
├── moderation.py    # escalade, DM, kick, réintégration
├── milestones.py    # paliers et messages de félicitations
├── stats.py         # classements, séries, récap hebdo
└── importer.py       # parsing des exports WhatsApp

tests/               # 67 tests, aucun ne nécessite de connexion WhatsApp
scripts/             # import_history, link_members, replay, backup
```

`gateway.py` est la seule pièce qui parle à `neonize` : tout le reste se
teste avec `tests/fakes.py::FakeGateway`, sans jamais toucher au réseau.

## Vie privée

Le bot stocke l'activité et les numéros de membres du groupe. L'export
WhatsApp réel (photos, historique) et le plan détaillé restent hors du dépôt
(`.gitignore`) — seul le code est versionné.
