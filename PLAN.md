# Local Flow Studio – Entwicklungsplan

## Ziel

Eine lokal auf Windows, macOS und Linux laufende Automationsumgebung mit visuellem Flow-Editor. Der erste Stand deckt den Kern ab: Knoten verbinden, Konfiguration, Ausführung, Protokolle, Import/Export sowie lokale und externe Integrationen. Vollständige Funktionsparität mit n8n ist ein Produktprogramm mit hunderten Connectoren, Multiuser-Betrieb, Versionsverwaltung und Skalierung; sie ist kein ehrliches Merkmal dieser ersten Version.

## Rollen und Verantwortung

| Rolle | Verantwortung | Ergebnis dieser Etappe |
| --- | --- | --- |
| Product Owner / CX | Anwendungsfälle, verständliche Abläufe, Prioritäten | Funktionsumfang, Editor, Vorlagen |
| UX/UI Designer | Navigation, Canvas, Formulare, Feedback und leere Zustände | Bedienoberfläche ohne JSON-Pflicht für Standardknoten |
| Workflow-Architekt | Graphmodell, Übergaben, Bedingungen, Trigger, Erweiterbarkeit | Persistiertes Flow-Schema und Laufzeit |
| Backend-Entwickler | API, SQLite, Ausführung, Zeitpläne, Protokolle | Lokaler Python-Server |
| Integrationsentwickler | LLM, HTTP, GitHub, Docker, Skripte | Austauschbare Knotentypen und Verbindungsprüfung |
| Security Engineer | Lokale Bindung, geheime Schlüssel, bewusste Ausführung | Umgebungsvariablen, Origin-/API-Schutz, Timeouts |
| QA / Release Engineer | Tests, Windows-Start, Paketierung, Dokumentation | Smoke-Tests, ZIP und Startanleitung |

Diese Rollen beschreiben Verantwortlichkeiten; die Umsetzung erfolgt in diesem Projektstand durch einen Entwickler.

## Umsetzungsphasen

1. **Nutzbarer lokaler Kern (dieses Paket):** Flows, Canvas, Knotenformulare, manuelle und Webhook-Ausführung, Intervallplan, Protokolle, Vorlagen, Import/Export. Knoten: Start, Text, Daten, Bedingung, HTTP, LLM (OpenAI-kompatibel), Python/JavaScript, Git, Docker, Pause, Ergebnis.
2. **Integrationen:** Credential-Tresor mit OS-Schlüsselbund, OAuth-Verbindungen, GitHub-App, Connector-SDK und zunächst die wichtigsten Dienste nach tatsächlichem Bedarf.
3. **Betrieb:** Warteschlange mit mehreren Workern, Versionen, Debugger mit schrittweiser Ausführung, Tests pro Knoten, Retry- und Fehlerpfade, Freigaben und Monitoring.
4. **n8n-Parität als Langfristziel:** Connector-Katalog, Subflows, Expressions, Trigger-Vielfalt, Multiuser-Rollen, Deployment- und Migrationswerkzeuge. Funktionsmatrix und Akzeptanztests je Ausbauschritt.

## Architektur

- Browser-Frontend lokal unter `http://127.0.0.1:8765`; der Server liefert UI und API aus einem Prozess.
- Python-Standardbibliothek, SQLite in `data/`, keine Pflichtpakete; Git, Docker und Node.js nur für jeweilige Funktionen nötig.
- Flow als gerichteter azyklischer Graph mit gespeicherten Positionen, Knoten-Konfiguration und beschrifteten Kanten für Bedingungen.
- Credentials bleiben in lokalen Umgebungsvariablen; ein Flow speichert ausschließlich den Variablennamen. Skript- und Docker-Knoten sind bewusst mächtig und laufen erst nach Auslösung des Flows.
- Erweiterung: Neue Knoten werden in der Registry des Servers und in den Felddefinitionen des Frontends ergänzt.

## Akzeptanz dieser Etappe

- Ein neuer Flow lässt sich ohne Kommandozeile im Browser erstellen, verbinden, konfigurieren, speichern und ausführen.
- Ausführungen zeigen Status, Knotenergebnisse und Fehlermeldungen; Daten überstehen einen Neustart.
- Webhook und Intervall lösen denselben Flow-Kern aus.
- Ein lokaler Python-Flow läuft ohne zusätzliche Pakete; fehlendes Docker/Git/Node wird klar gemeldet.
- Die App ist ausschließlich an Loopback gebunden; zustandsändernde API-Aufrufe benötigen einen Sitzungsschlüssel.

## Funktionsstand gegenüber dem Ziel n8n

| Bereich | In diesem Paket | Ausbau |
| --- | --- | --- |
| Flows | Visueller Editor, Verzweigungen, Datenübergabe, Import/Export | Subflows, Schleifen, Versionsvergleich, Debugger |
| Trigger | Manuell, Webhook, Intervall | Cron-Kalender, app-spezifische Events, Trigger-Katalog |
| Integrationen | HTTP, OpenAI-kompatible LLMs, Claude Messages API, Mistral AI, GitHub/Git, Docker, Python, JavaScript | OAuth, Connector-SDK, App-Katalog |
| Betrieb | SQLite, Laufhistorie, Knotenresultate | Queue, Retry, Worker, Skalierung, Benachrichtigungen |
| Zugang | Loopback, Sitzungstoken, Schlüssel nur im Arbeitsspeicher | OS-Schlüsselbund, Rollen und Benutzerverwaltung |

Die übrigen n8n-Funktionen sind geplante Erweiterungen und in dieser Version nicht implementiert.
