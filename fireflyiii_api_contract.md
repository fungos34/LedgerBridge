````md
# Firefly III – Transaction Import JSON (API Contract)

Diese Doku beschreibt **das JSON-Format**, das du direkt an die **Firefly III API** schicken kannst, um Buchungen (auch Splits) anzulegen – also genau das, was deine Paperless→Parser→Firefly-Pipeline am Ende ausspucken soll.

Quelle der Felder: Firefly-III OpenAPI/Client-Modelle (TransactionStore + TransactionSplitStore). :contentReference[oaicite:0]{index=0}

---

## 1) Endpoint (dein Setup / LAN)

### Create Transaction(s)
- **POST** `http://{FIRELFY_HOST}:{FIREFLY_PORT}/api/v1/transactions`

> In deinem Setup ist das sehr wahrscheinlich:  
> `http://192.168.1.138:8081/api/v1/transactions`  
> (weil du Firefly auf 8081 per UFW freigegeben hast)

### Header
- `Authorization: Bearer {PERSONAL_ACCESS_TOKEN}`
- `Content-Type: application/json`
- `Accept: application/json`

---

## 2) Request Body – Root: `TransactionStore`

### Schema
```json
{
  "error_if_duplicate_hash": false,
  "apply_rules": true,
  "fire_webhooks": true,
  "group_title": "optional title for a split group",
  "transactions": [ /* 1..n TransactionSplitStore */ ]
}
````

### Felder (Root)

* `transactions` (**required**, Array<`TransactionSplitStore`>) ([Docs.rs][1])
* `error_if_duplicate_hash` (optional bool) – bricht ab, wenn Duplikat erkannt wird ([Docs.rs][1])
* `apply_rules` (optional bool) – Firefly-Regeln anwenden ([Docs.rs][1])
* `fire_webhooks` (optional bool, default true) ([Docs.rs][1])
* `group_title` (optional string) – Titel für Split-Transaktion ([Docs.rs][1])

---

## 3) Request Body – Split: `TransactionSplitStore` (das eigentliche “Import-Modell”)

### Minimal **gültig** (pro Split)

Diese 4 Felder sind faktisch der Kern:

* `_type` (required)
* `date` (required)
* `amount` (required)
* `description` (required) ([Docs.rs][2])

```json
{
  "_type": "withdrawal",
  "date": "2026-01-05",
  "amount": "11.40",
  "description": "BILLA GRAZ"
}
```

### `_type` (TransactionTypeProperty)

Typen sind u.a.:

* `"withdrawal"` (Ausgabe)
* `"deposit"` (Einnahme)
* `"transfer"` (Umbuchung)

(Genaues Enum hängt von Firefly-Version ab; in der Praxis sind das die üblichen Werte.)

---

## 4) “Maximal ausführlich” – Beispiel JSON (alles drin, was das Modell hergibt)

> Das ist der **vollste** TransactionStore, den du realistischerweise erzeugen kannst – inklusive SEPA-Metadaten, Fremdwährung, Budgets, Kategorien, Tags, Referenzen, Datenfeldern etc.
> Du wirst davon in der Pipeline meistens nur 10–30% füllen.

```json
{
  "error_if_duplicate_hash": false,
  "apply_rules": true,
  "fire_webhooks": true,
  "group_title": "Split: Monats-Einkauf (Belege in Paperless)",
  "transactions": [
    {
      "_type": "withdrawal",
      "date": "2026-01-05",
      "amount": "35.70",
      "description": "SPAR FIL. 5631 GRAZ",

      "order": 1,

      "currency_id": "1",
      "currency_code": "EUR",

      "foreign_amount": null,
      "foreign_currency_id": null,
      "foreign_currency_code": null,

      "budget_id": "3",
      "budget_name": "Lebensmittel",

      "category_id": "12",
      "category_name": "Supermarkt",

      "source_id": "1",
      "source_name": "Girokonto (Easybank)",

      "destination_id": null,
      "destination_name": "SPAR",

      "reconciled": false,

      "piggy_bank_id": null,
      "piggy_bank_name": null,

      "bill_id": null,
      "bill_name": null,

      "tags": ["receipt", "paperless", "groceries"],
      "notes": "Paperless doc_id=1234; confidence=0.93; matched bank tx=easybank:2026-01-05:35.70",

      "internal_reference": "paperless:1234",
      "external_id": "easybank:tx:ABCDEF123456",
      "external_url": "http://192.168.1.138:8000/documents/1234",

      "bunq_payment_id": null,

      "sepa_cc": "AT",
      "sepa_ct_op": "AT12 3456 7890 1234 5678",
      "sepa_ct_id": "E2E-ID-XYZ",
      "sepa_db": "MANDATE-123",
      "sepa_country": "AT",
      "sepa_ep": "OTHR",
      "sepa_ci": "AT98ZZZ00000000000",
      "sepa_batch_id": "BATCH-2026-01",

      "interest_date": null,
      "book_date": "2026-01-05",
      "process_date": "2026-01-05",
      "due_date": null,
      "payment_date": "2026-01-05",
      "invoice_date": "2026-01-04"
    }
  ]
}
```

Alle optionalen Felder in `TransactionSplitStore` sind in der Modellliste als `[optional]` markiert. ([Docs.rs][2])

---

## 5) Welche Felder sind optional?

### Required (pro Split)

* `_type`
* `date`
* `amount`
* `description` ([Docs.rs][2])

### Optional (Auszug; **alles** außer den 4 Required)

* Kontierung: `source_id/source_name`, `destination_id/destination_name` ([Docs.rs][2])
* Kategorie/Budget: `category_id/category_name`, `budget_id/budget_name` ([Docs.rs][2])
* Tags/Notizen: `tags`, `notes` ([Docs.rs][2])
* Idempotenz/Linking: `internal_reference`, `external_id`, `external_url` ([Docs.rs][2])
* Fremdwährung: `foreign_amount` + (`foreign_currency_id` oder `foreign_currency_code`) ([Docs.rs][2])
* SEPA-Felder: `sepa_*` ([Docs.rs][2])
* Datumsfelder: `book_date`, `process_date`, `due_date`, `payment_date`, `invoice_date`, `interest_date` ([Docs.rs][2])

---

## 6) Response (typische Struktur)

Die API liefert ein `TransactionSingle` zurück, mit `data` (=TransactionRead). ([Docs.rs][3])
`TransactionRead` enthält `type`, `id`, `attributes` und `links`. ([Docs.rs][4])
Innerhalb `attributes` steckt u.a. `group_title` und `transactions` (Liste der Splits). ([Docs.rs][5])

### Beispiel Response (gekürzt, aber strukturecht)

```json
{
  "data": {
    "type": "transactions",
    "id": "98765",
    "attributes": {
      "group_title": "Split: Monats-Einkauf (Belege in Paperless)",
      "transactions": [
        {
          "type": "withdrawal",
          "date": "2026-01-05",
          "amount": "35.70",
          "description": "SPAR FIL. 5631 GRAZ"
        }
      ]
    },
    "links": {
      "self": "http://192.168.1.138:8081/api/v1/transactions/98765"
    }
  }
}
```

---

## 7) Fehlerfälle (was du erwarten sollst)

### Validierungsfehler (typisch)

Wenn z.B. `date` leer ist, bekommst du genau den Fehler, den du schon gesehen hast (`transactions.0.date is required`).
Ursache: Du erzeugst JSON, bei dem der Split kein `date` enthält oder `""` ist – das ist **nicht zulässig**, weil `date` required ist. ([Docs.rs][2])

---

## 8) Praktischer Import-Standard für eure Pipeline (empfohlen)

Für eure Paperless→Parser→Firefly-Pipeline würde ich **minimal stabil** immer liefern:

**Immer setzen**

* `_type`, `date`, `amount`, `description`
* `source_name` (z.B. “Girokonto (Easybank)”)
* `destination_name` (Merchant / Gegenkonto, notfalls “Unbekannt”)
* `external_id` (eindeutig! z.B. `paperless:{doc_id}` oder Bank-Transaction-ID)
* `notes` (mit Confidence/Extractor-Version/Doc-Link)

**Optional je nach Confidence**

* `category_name`, `budget_id`, `tags`, `invoice_date`, `payment_date`, `sepa_*`

Damit hast du:

* Idempotenz (kein Duplikat-Chaos),
* nachvollziehbare Provenienz,
* und human-in-the-loop kann gezielt nur die “wackeligen” Felder abnicken.

---

```

Wenn du willst, kann ich dir als nächsten Schritt eine **konkrete Mapping-Strategie Paperless→Firefly** als “Confidence Contract” (low/medium/high) in genau diesem Format ausformulieren – inkl. Feldern, die bei euch *niemals* automatisch gesetzt werden (z.B. Budget/Kategorie nur ab Confidence ≥ X).
::contentReference[oaicite:20]{index=20}
```

[1]: https://docs.rs/crate/firefly-iii/latest/source/docs/TransactionStore.md "firefly-iii 0.2.5 - Docs.rs"
[2]: https://docs.rs/crate/firefly-iii/latest/source/docs/TransactionSplitStore.md "firefly-iii 0.2.5 - Docs.rs"
[3]: https://docs.rs/crate/firefly-iii/latest/source/docs/TransactionSingle.md "firefly-iii 0.2.5 - Docs.rs"
[4]: https://docs.rs/crate/firefly-iii/latest/source/docs/TransactionRead.md "firefly-iii 0.2.5 - Docs.rs"
[5]: https://docs.rs/crate/firefly-iii/latest/source/docs/Transaction.md "firefly-iii 0.2.5 - Docs.rs"
