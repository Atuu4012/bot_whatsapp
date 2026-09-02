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
| **Valider** | Un message conforme = 1 photo + le(s) numéro(s) attendu(s) (compteur + 1, éventuellement plusieurs numéros consécutifs pour rattraper plusieurs bières d'un coup), avec un peu de texte/emoji toléré autour. Légende oubliée ? Le numéro envoyé juste après dans un message séparé complète la photo. |
| **Sanctionner** | Message non conforme → DM d'explication puis expulsion |
| **Rattraper** | Numéro sauté (jusqu'à 50) → la bière est comptée quand même, le trou est comblé par une ligne « - » et l'auteur reçoit un simple avertissement. S'il corrige la légende de sa photo, la bière est renumérotée et le « - » se décale vers le numéro devenu manquant — sans prévenir ceux qui ont posté entre-temps, qui n'y sont pour rien. |
| **Compter** | Le compteur est toujours `MAX(number)` en base, jamais une variable en mémoire |
| **Reconnaître** | Un membre qui poste pour la première fois adopte automatiquement l'historique importé à son nom (`scripts/match_members.py` pour le reste) |
| **Célébrer** | Message automatique dans le groupe aux paliers (1000, 2500, 5000, tous les 500…) |
| **Statistiquer** | Classements, séries, récap hebdomadaire |

L'escalade des sanctions : 1ʳᵉ infraction → 24 h (retour auto), 2ᵉ → 7 jours
(retour auto), 3ᵉ → retour validé par un admin. Une infraction se prescrit
au bout de 90 jours sans récidive.

## État du projet

- ✅ **Phase locale** — base de données, validateur, moteur de décision,
  modération, paliers, stats, parsing de l'export, import de l'historique.
- ✅ **Phase WhatsApp** — connexion réelle via [neonize](https://github.com/krypton-byte/neonize)
  (protocole whatsmeow) : appairage, réception des messages, légendes
  éditées déchiffrées, rattrapage des numéros sautés.
- ⏳ **Observation** — le bot tourne en `DRY_RUN=true` : il compte, il
  journalise, il ne sanctionne pas. Rien ne passe en modération réelle avant
  deux semaines de relecture des logs.

180 tests, aucun ne nécessite de connexion WhatsApp.

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

Vérifier les règles de validation contre l'historique réel (aucune écriture
en base, aucune connexion WhatsApp) :

```bash
python scripts/replay.py export.txt
```

Pour mettre le bot en service, suivre *Lancer le bot* ci-dessous.

## Lancer le bot

### 1. Le téléphone du bot — une seule fois

L'iPhone qui porte la SIM dédiée **n'exécute aucun code du projet**. Il garde
WhatsApp ouvert et sert d'« appareil principal » : le bot s'y rattache comme
*appareil lié*.

1. SIM dédiée dans le téléphone, WhatsApp installé, compte créé.
2. Nom et photo de profil (« 🤖 BeerBot »). Ce n'est pas cosmétique : un DM
   automatique venant d'un numéro inconnu sans photo est le profil type que
   WhatsApp signale comme spam.
3. Faire enregistrer le numéro dans le répertoire de quelques membres.
4. Ajouter le bot au groupe. **Ne le passe pas admin tout de suite** : admin
   ne sert qu'à expulser, et rien ne doit pouvoir expulser pendant
   l'observation.
5. Laisser le téléphone branché en wifi, écran éteint. S'il ne se reconnecte
   jamais, WhatsApp finit par délier ses appareils.

### 2. Appairer — une seule fois

```bash
python scripts/pair.py        # ou --phone 33612345678 pour un code à 8 caractères
```

Le script affiche un QR à scanner depuis l'iPhone (*Réglages → Appareils
liés → Lier un appareil*), puis liste les groupes du bot :

```
[OK] Connecté en tant que 33600000000:1@s.whatsapp.net (BeerBot)

Groupes du bot — copier le JID voulu dans BOT_GROUP_JID :
  120363XXXXXXXXXXXX@g.us            [admin    ] Les Bières
```

La session est écrite dans `data/session.db`. **Elle vaut un accès complet au
compte** : jamais dans Git (`/data/` est ignoré), et c'est ce fichier qu'on
recopie tel quel sur la machine qui hébergera le bot en 24/7.

### 3. Remplir le `.env`

| Clé | À quoi ça sert |
|---|---|
| `BOT_GROUP_JID` | Le groupe surveillé, tel qu'affiché par `pair.py`. Tout ce qui vient d'ailleurs (DM, autres groupes) est ignoré. |
| `ADMIN_JIDS` | Les humains qui peuvent parler librement sans être sanctionnés, séparés par des virgules. |
| `DRY_RUN` | `true` pendant l'observation. Voir le tableau de l'étape 5. |
| `DB_PATH` | `data/beerbot.db`. Mets une base à part (`data/beerbot-test.db`) si tu joues sur un groupe de test. |
| `GAP_WARNING_DELAY_SECONDS` | Délai avant d'avertir d'un numéro sauté — le temps de se corriger soi-même. |

### 4. Caler le compteur — avant **chaque** lancement

Le bot ne voit que ce qui se poste pendant qu'il tourne. S'il démarre en
retard sur le groupe, la première bière reçue déclenche le rattrapage : les
numéros manqués deviennent des lignes « - » et leurs auteurs perdent leur
attribution.

```bash
# WhatsApp → le groupe → Exporter la discussion → Sans médias
python scripts/import_history.py _chat.txt membres.csv data/beerbot.db
```

L'import ne réinsère que les numéros absents : le relancer sur une base
existante est sans danger. Vérifier où en est le compteur :

```bash
python -c "import sqlite3;print(sqlite3.connect('data/beerbot.db').execute('select max(number) from beers').fetchone()[0])"
```

Ce nombre doit être celui de la dernière bière postée dans le groupe.

### 5. Lancer

```bash
python -m src.main
```

```
WARNING beerbot: DRY_RUN actif : aucune sanction réelle ne sera appliquée.
INFO beerbot: BeerBot prêt (groupe=120363XXXXXXXXXXXX@g.us, dry_run=True)
INFO whatsmeow.Client: Successfully authenticated
INFO beerbot: photo de Alix  | légende='869' -> ACCEPTED
INFO beerbot: photo de Milos | légende=None -> AWAITING_CAPTION
INFO beerbot: texte de Milos | légende='870' -> ACCEPTED
INFO beerbot: photo de Hugzzz | légende='874' -> ACCEPTED_WITH_GAP
INFO beerbot: photo de Hugzzz | légende='871-872-873-874' -> CORRECTED
```

Une ligne par message, et rien entre-temps : les balayages périodiques
tournent en silence. `Ctrl+C` arrête le bot, la session reste valable.

Ce que `DRY_RUN` change :

| | `DRY_RUN=true` | `DRY_RUN=false` |
|---|---|---|
| Compter les bières en base | ✅ | ✅ |
| Journaliser les décisions | ✅ | ✅ |
| Enregistrer les infractions | ✅ (`action='dry_run'`) | ✅ (`warned` / `kicked`) |
| DM d'explication, expulsion | ❌ | ✅ |
| Avertissement « numéro sauté » | ❌ | ✅ |
| Message de palier, récap hebdo | ❌ | ✅ |

### 6. Pendant les deux semaines d'observation

Relis le journal tous les soirs : c'est là que se voient les cas non
anticipés. Ce qui aurait été sanctionné :

```bash
python -c "import sqlite3;[print(dict(r)) for r in sqlite3.connect('data/beerbot.db').execute('select * from infractions order by created_at desc limit 20')]"
```

Les membres qui postent se font reconnaître automatiquement. Pour les autres,
compléter le mapping nom → JID :

```bash
python scripts/match_members.py data/beerbot.db membres.csv --export _chat_.txt
```

⚠️ **Ne fais pas tourner `scripts/probe_events.py` en même temps que le
bot** : les deux partagent `data/session.db`, donc l'état du ratchet Signal,
et ça finit en messages indéchiffrables. Si tu dois disséquer un événement
pendant que le bot tourne, appaire la sonde comme un **second appareil lié**,
avec sa propre session :

```bash
SESSION_PATH=data/session-probe.db python scripts/pair.py
SESSION_PATH=data/session-probe.db python scripts/probe_events.py --dump data/probe.log
```

À chaque redémarrage, les bières postées pendant l'arrêt manquent à l'appel :
réimporte un export frais avant de relancer (étape 4).

### 7. Passer en modération réelle

Dans cet ordre, et pas avant d'avoir relu les logs :

1. Passer le bot **admin** du groupe — sans ça, aucune expulsion n'est possible.
2. **Annoncer la règle dans le groupe.** Personne ne doit découvrir le bot en
   se faisant expulser.
3. `DRY_RUN=false` dans le `.env`, puis relancer `python -m src.main`.
4. Verrouiller le groupe : approbation des nouveaux membres, lien
   d'invitation révoqué.

### 8. Dépannage

| Ce que tu vois | Ce que c'est |
|---|---|
| `BOT_GROUP_JID manquant dans .env` | `.env` pas rempli, ou lancé depuis un autre dossier. |
| `Aucune session dans data/session.db` | Pas encore appairé : `python scripts/pair.py`. |
| `[ERREUR] WhatsApp a déconnecté ce compte lié` | Session révoquée. Supprime `data/session.db` et réappaire. |
| `édition du message X ignorée : secret inconnu` | Légende éditée dont le message d'origine est antérieur au démarrage du bot. Sans gravité : le message reste jugé sur sa légende d'origine. |
| `message ignoré, conversion ou traitement en échec` | Un message n'a pas pu être traité, le traceback suit dans le journal. Le bot continue. |
| Aucune ligne `photo de …` alors que ça poste | Mauvais `BOT_GROUP_JID` : tout ce qui vient d'un autre chat est ignoré en silence. |

## Structure

```
src/
├── main.py         # câblage des dépendances, point d'entrée
├── config.py       # chargement du .env
├── gateway.py      # abstraction du protocole WhatsApp (Protocol + NeonizeGateway)
├── engine.py       # reçoit un message, décide, agit
├── db.py           # schéma SQLite + accès aux données
├── identity.py     # rapproche l'historique importé des JID réels
├── validator.py    # conformité d'un message (photo + légende)
├── moderation.py   # escalade, DM, kick, réintégration
├── milestones.py   # paliers et messages de félicitations
├── stats.py        # classements, séries, récap hebdo
└── importer.py     # parsing des exports WhatsApp

tests/              # 180 tests, aucun ne nécessite de connexion WhatsApp
scripts/            # pair, probe_events, match_members, import_history,
                    # link_members, replay, backup
```

`gateway.py` est la seule pièce qui parle à `neonize` : tout le reste se
teste avec `tests/fakes.py::FakeGateway`, sans jamais toucher au réseau.

## Vie privée

Le bot stocke l'activité et les numéros de membres du groupe. L'export
WhatsApp réel (photos, historique), le mapping `membres.csv` et le plan
détaillé restent hors du dépôt (`.gitignore`) — seul le code est versionné.
