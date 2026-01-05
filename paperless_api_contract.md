````md
# Paperless-ngx → Finance Extraction Contract  
**Version:** 1.0  
**Purpose:** Deterministische, auditierbare und reproduzierbare Extraktion von Finanzdaten aus Paperless-ngx zur Weiterverarbeitung (z. B. Firefly III).

Dieses Dokument ist **kein Tutorial**, sondern ein **API-/Datenvertrag**.  
Jedes Feld ist definiert mit:
- Herkunft (woher kommt es?)
- Zweck (wofür brauchen wir es?)
- Format (wie muss es aussehen?)
- Nutzen (warum existiert es?)
- Konsequenzen, wenn es fehlt

Nichts wird dem Zufall überlassen.

---

## 0. Grundprinzipien (nicht verhandelbar)

1. **Paperless ist die Dokument-SSOT**  
   → Keine Finanzbuchung ohne referenziertes Paperless-Dokument.

2. **Parser ist deterministisch**  
   → Gleiche Eingabe → gleiches Ergebnis.

3. **Explizite Confidence statt impliziter Magie**  
   → Jeder automatisch extrahierte Wert hat eine Bewertung.

4. **Human-in-the-loop ist Teil des Systems, kein Workaround**

---

## 1. Gesamtstruktur des Extraktions-Outputs

Dies ist das **kanonische Output-Format**, das **immer** aus Paperless erzeugt wird, bevor irgendeine Buchungslogik greift.

```json
{
  "paperless_meta": { ... },
  "document_classification": { ... },
  "files": { ... },
  "textual_content": { ... },
  "extracted_finance_data": { ... },
  "line_items": [ ... ],
  "confidence": { ... },
  "provenance": { ... }
}
````

---

## 2. `paperless_meta` – Dokument-Identität & technische Herkunft

```json
"paperless_meta": {
  "document_id": 12345,
  "title": "SPAR Einkauf 18.11.2024",
  "created": "2024-11-18",
  "added": "2024-11-19T08:14:22Z",
  "modified": "2024-11-19T08:15:01Z",
  "archive_serial_number": 7421
}
```

### Felder

| Feld                    | Herkunft      | Zweck                      | Format     | Nutzen                                              |
| ----------------------- | ------------- | -------------------------- | ---------- | --------------------------------------------------- |
| `document_id`           | Paperless API | Primärschlüssel            | int        | **Globale Referenz**, wird `external_id` in Firefly |
| `title`                 | Paperless     | Menschliche Orientierung   | string     | Fallback für Beschreibung                           |
| `created`               | Paperless     | Belegdatum (falls gesetzt) | YYYY-MM-DD | Kandidat für Rechnungsdatum                         |
| `added`                 | Paperless     | Systemzeitpunkt            | ISO-8601   | Audit, Debug                                        |
| `modified`              | Paperless     | Änderungsverfolgung        | ISO-8601   | Re-Parsing-Trigger                                  |
| `archive_serial_number` | Paperless     | Archiv-Referenz            | int        | Rechtliche Nachvollziehbarkeit                      |

**Warum das wichtig ist:**
Ohne diese Felder gibt es **keine saubere Rückverfolgbarkeit** zwischen Buchung und Dokument.

---

## 3. `document_classification` – Semantische Einordnung

```json
"document_classification": {
  "document_type": "Receipt",
  "correspondent": "SPAR",
  "tags": ["finance/inbox", "receipt"],
  "storage_path": "Finance/2024/11"
}
```

### Felder

| Feld            | Herkunft  | Zweck              | Format      | Nutzen                       |
| --------------- | --------- | ------------------ | ----------- | ---------------------------- |
| `document_type` | Paperless | Typisierung        | enum/string | Parser-Strategie             |
| `correspondent` | Paperless | Aussteller/Händler | string      | Payee-Erkennung              |
| `tags`          | Paperless | Workflow-Status    | string[]    | Routing (parsed/manual/etc.) |
| `storage_path`  | Paperless | Archivkontext      | string      | Kontext, optional            |

**Warum das wichtig ist:**
Ohne Klassifikation weiß das System **nicht**, ob es sich um Rechnung, Vertrag, Kassenbeleg oder Müll handelt.

---

## 4. `files` – Physische Dokumente & Embedded Data

```json
"files": {
  "original_filename": "receipt_18112024.pdf",
  "mime_type": "application/pdf",
  "has_original": true,
  "original_download_url": "/api/documents/12345/download/?original=true",
  "archived_download_url": "/api/documents/12345/download/",
  "contains_embedded_xml": true,
  "embedded_xml_type": "Factur-X"
}
```

### Felder

| Feld                    | Herkunft  | Zweck                 | Nutzen              |
| ----------------------- | --------- | --------------------- | ------------------- |
| `mime_type`             | Paperless | Dateityp              | Parser-Entscheidung |
| `has_original`          | Paperless | Strukturverfügbarkeit | Original ≠ OCR      |
| `contains_embedded_xml` | Parser    | Strukturindikator     | Line-Items möglich  |
| `embedded_xml_type`     | Parser    | Standardkennung       | **Goldstandard**    |

**Warum das wichtig ist:**
Strukturierte Rechnungen (Factur-X, UBL) erlauben **exakte Artikelauflösung**.
OCR-Text allein **niemals zuverlässig**.

---

## 5. `textual_content` – OCR & extrahierter Text

```json
"textual_content": {
  "ocr_text": "Gesamtbetrag EUR 35,70 ...",
  "language": "de",
  "text_quality_score": 0.88
}
```

| Feld                 | Zweck             | Nutzen                   |
| -------------------- | ----------------- | ------------------------ |
| `ocr_text`           | Fallback-Quelle   | Betrag/Datum-Heuristiken |
| `language`           | Parsing-Regeln    | Locale-abhängige Muster  |
| `text_quality_score` | Risikoabschätzung | Confidence-Basis         |

---

## 6. `extracted_finance_data` – Zusammengefasste Buchungsdaten

```json
"extracted_finance_data": {
  "currency": "EUR",
  "total_gross": "35.70",
  "total_net": "29.75",
  "tax_amount": "5.95",
  "tax_rate": "20%",
  "invoice_date": "2024-11-18",
  "payment_date": "2024-11-18",
  "invoice_number": "R-2024-11832",
  "payment_reference": "MC/000010831"
}
```

### Pflicht vs Optional

| Feld           | Pflicht  | Warum                     |
| -------------- | -------- | ------------------------- |
| `total_gross`  | **Ja**   | Ohne Betrag keine Buchung |
| `currency`     | **Ja**   | Firefly benötigt sie      |
| `invoice_date` | **Ja**   | Firefly `date`            |
| Rest           | Optional | Anreicherung              |

---

## 7. `line_items` – Artikel / Positionen (optional, aber mächtig)

```json
"line_items": [
  {
    "position": 1,
    "description": "Butter 250g",
    "quantity": 1,
    "unit_price": "2.49",
    "total": "2.49",
    "tax_rate": "10%"
  }
]
```

### Herkunft

* **Embedded XML:** exakt, hohe Confidence
* **PDF-Tabellen:** heuristisch
* **OCR-Text:** nur experimental

### Nutzung

* Aufsplitten auf Kategorien
* Detaillierte Budget-Analyse
* Steueraufteilung

---

## 8. `confidence` – Explizite Unsicherheiten

```json
"confidence": {
  "overall": 0.93,
  "amount": 0.99,
  "date": 0.95,
  "line_items": 0.40,
  "classification": "HIGH"
}
```

**Warum das existiert:**
Automatisierung ohne Vertrauen ist Selbstbetrug.
Diese Werte steuern:

* Auto-Import
* Review-Pflicht
* UI-Markierungen

---

## 9. `provenance` – Nachvollziehbarkeit & Audit

```json
"provenance": {
  "source_system": "paperless",
  "parser_version": "finance-parser-v0.3.1",
  "parsed_at": "2026-01-06T10:14:00Z",
  "ruleset": "invoice-de-v2"
}
```

**Nutzen:**

* Reproduzierbarkeit
* Regressionstests
* Rechtliche Nachvollziehbarkeit

---

## 10. Warum dieser Vertrag existiert

Ohne diesen Vertrag:

* entstehen implizite Annahmen
* Fehler sind nicht reproduzierbar
* Automatisierung ist nicht vertrauenswürdig

Mit diesem Vertrag:

* klare Übergaben zwischen Systemen
* kontrollierte Automatisierung
* saubere Human-in-the-loop-Integration
* Firefly-Import wird trivial

---

## 11. Nächster logischer Schritt

Auf Basis **dieses** Vertrags können wir jetzt:

1. ein **Parser-Decision-Tree** (A/B/C) definieren
2. das **exakte Firefly-Import-Mapping** 1:1 ableiten
3. eine **Review-UI-Spezifikation** bauen

Sag mir, welcher davon als Nächstes drankommt.

```
```
