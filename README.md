# Local Flow Studio

Ein lokal laufender visueller Flow-Editor für Automationen, LLMs, GitHub, Docker und Skripte.

## Start

**Windows:** Python 3.10+ installieren, ZIP entpacken und `start-windows.bat` doppelklicken. **macOS/Linux:** `python3 app.py` im entpackten Ordner ausführen. Danach `http://127.0.0.1:8765` öffnen. Der Server benötigt keine Python-Pakete. Daten liegen in `data/flows.db` und bleiben auf deinem Rechner.

## Erster Flow

1. „Neuer Flow“ wählen oder die Vorlage „KI-Recherche“ laden.
2. Knoten im Canvas hinzufügen und per Klick auf den Ausgang und dann auf den Eingang verbinden. Bei Bedingungen den Ausgang „Ja“ oder „Nein“ wählen.
3. Knoten anklicken und rechts konfigurieren. Felder verstehen `{{trigger.name}}`, `{{input.text}}`, `{{nodes.KNOTEN_ID.text}}` und `{{env.MEIN_SCHLUESSEL}}`. Ein Feld, das nur einen Ausdruck enthält, übernimmt auch Zahlen und Objekte.
4. „Speichern“ und „Flow starten“ wählen. Im Tab „Läufe“ erscheinen Ergebnisse und Fehler pro Knoten.

## Verbindungen

- **LLM:** Im LLM-Knoten den Anbieter auswählen. **OpenAI / kompatibel** verwendet `OPENAI_API_KEY` oder einen lokalen Endpoint wie Ollama (`http://127.0.0.1:11434/v1`, ohne Schlüssel). **Claude** verwendet `ANTHROPIC_API_KEY`, die Anthropic Messages API und eine konfigurierbare Grenze für Ausgabetoken. **Mistral AI** verwendet `MISTRAL_API_KEY` und die Chat Completions API. Schlüssel unter „Verbindungen“ für diese Sitzung eingeben; die Modell-ID und Basis-URL lassen sich im Knoten ändern. Bestehende Flows ohne Anbieterfeld bleiben OpenAI-kompatibel.
- **GitHub:** Unter „Verbindungen“ `GITHUB_TOKEN` mit einem Token für diese Sitzung verbinden. Der Tab prüft den Zugriff und klont ein Repository in den lokalen Ordner `data/repos/`. Git muss installiert sein. Ein Git-Knoten arbeitet auf einem dieser Klone; `commit_push` schreibt und pusht ausdrücklich.
- **Docker:** Docker Desktop/Engine installieren und starten. Der Tab „Verbindungen“ zeigt laufende Container. Ein Docker-Knoten führt `docker exec` in einem bestehenden Container aus. Er kann auf dessen Daten zugreifen.
- **Skripte:** Python ist verfügbar; JavaScript benötigt Node.js. `input` enthält die Ausgabe des Vorgängers. Ergebnis in `output` setzen, etwa `output = {"text": input["text"].upper()}`. Skripte laufen lokal mit den Rechten des angemeldeten Benutzers.
- **Webhook:** Im Flow einen Webhook-Startknoten anlegen. Der Editor zeigt die URL. `POST` mit JSON-Body und dem im Flow hinterlegten Token startet den Flow; Beispiel: `curl -X POST -H "Content-Type: application/json" -d '{"name":"Felix"}' 'http://127.0.0.1:8765/api/webhook/FLOW_ID/TOKEN'`.
- **Intervall:** Startknoten „Zeitplan“ mit Intervall in Sekunden. Der Server muss laufen; Jobs werden beim Start neu registriert und sind kein exakter Cron-Ersatz.

## Sicherheit und Grenzen

Die App bindet nur an `127.0.0.1`; die Oberfläche verwendet einen kurzlebigen Sitzungsschlüssel für ihre API. Webhooks verwenden eigene Token. Setze keine fremden Flows unbesehen in Gang: Skript-, Git- und Docker-Knoten können Dateien verändern. Schlüssel aus „Verbindungen“ bleiben bis zum Server-Neustart im Prozessspeicher; sie werden nicht in die Datenbank geschrieben. Verwende für HTTP-Zugangsdaten `{{env.NAME}}`, statt sie direkt in Felder zu schreiben. Exportierte Flows enthalten Konfiguration und Webhook-Token, daher Exporte vertraulich behandeln.

Dies ist ein funktionsfähiger lokaler Startstand, **keine vollständige n8n-Nachbildung**. Es fehlen unter anderem der große Connector-Katalog, OAuth, Secret-Vault, Subflows, kollaborative Rollen, Debugger, Distributed Worker und produktionsreife Queue. Für eine öffentliche Bereitstellung ist die App nicht ausgelegt.

## Tests

`python3 -m unittest discover -s tests -v`

## Struktur

`app.py` – Server, Datenhaltung und Ausführung; `web/` – Frontend; `tests/` – Kern- und API-Tests; `PLAN.md` – Rollen und Ausbauplan.
