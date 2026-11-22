# clipgang-tracker

Tracker, Schnitt und Formate für Clipgang. Alles läuft über **Actions**.

## So bekommst du ein fertiges Video

1. Die Morgen-Auswertung von Claude endet mit einer Format-Auswahl. Du antwortest dort mit der Nummer, Claude schickt dir eine Zeile, die mit `{` beginnt.
2. Hier im Repo: **Actions → „Auftrag ausfuehren“ → Run workflow** → Zeile einfügen → starten.
3. Nach 2–6 Minuten: Lauf öffnen → unten bei **Artifacts** „fertig“ laden.

Vorschaubilder landen im Branch `vorschau`.

## Formate

| Format | Was rauskommt |
|---|---|
| `einzel` | ein Clip im Standard- oder Facecam-Layout, optional mit Untertiteln, Auto-Start an der Pointe, Teil 1/2 |
| `versus` | zwei Streamer übereinander, nacheinander abgespielt, Hook auf der Trennlinie |
| `countdown` | 3–5 Clips hintereinander mit #3 → #2 → #1 |
| `zusammenschnitt` + `"modus":"namen"` | z. B. „3 Streamer, gleiches Finale“, Etikett = Streamer-Name |
| `rennen` | Story-Rennen: animiertes Ranking aus den Stream-Titeln, ohne fremde Clips |

Beispiele:

```
{"format":"einzel","link":"https://www.twitch.tv/zarbex/clip/...","hook":"Zarbex kann's nicht glauben 😳","layout":"facecam","untertitel":true,"start":"auto"}
{"format":"versus","links":["<clip A>","<clip B>"],"hook":"Wer crasht besser? 💀","untertitel":true,"start":["auto","auto"],"dauer":20}
{"format":"countdown","links":["<#3>","<#2>","<#1>"],"hook":"Die härtesten Crashes heute 💥","start":"auto","dauer":15}
{"format":"zusammenschnitt","modus":"namen","links":["<clip>","<clip>","<clip>"],"hook":"3 Streamer, gleiches Finale 😳"}
{"format":"rennen","hook":"Wer ist zuerst durch? 😳"}
```

## Optionen

- `untertitel: true`: Untertitel Wort für Wort in der Bildmitte, das aktive Wort ist pink. Die Hook wandert nach oben; beim Facecam-Layout sitzt sie in einem eigenen Streifen.
- `start: "auto"`: Der Clip beginnt 2,5 s vor der lautesten Stelle (Schrei, Lachen), damit die Pointe sofort kommt.
- `dauer`: maximale Länge in Sekunden. `teile: 2` schneidet in Teil 1 und Teil 2.
- `layout: "facecam"` geht nur für Streamer mit gespeicherter Facecam-Position (`config.json` → `facecams`). Sonst wird automatisch Standard genommen. Die Position misst du im Raster-Vorschaubild aus.

Der alte Workflow **„Clip schneiden“** funktioniert weiter und hat jetzt auch Untertitel, Auto-Start und Teile.
