## Forecast generator (Deepnote/runner) — doporučené cron schedule

### Co tím řešíme
- **CZ**: D‑1 musí být v `13_*`/`14_*` nejpozději do **07:00 Europe/Prague**.
- **non‑CZ**: ráno je **OK mít D‑2**, D‑1 je požadovaný až **od 14:00 Europe/Prague**.

Tento soubor říká **kdy spouštět generátor** (Deepnote notebook / jiný runner), který zapisuje forecast CSV do `gs://13_final_forecasts_*/*.csv`.

### Princip (jednoduché pravidlo)
- `13_*` tabulky jsou plněné přes **DTS google_cloud_storage**, které typicky startuje v konkrétní **minutě v hodině (UTC)**.
- Ideální je, aby generátor přepsal CSV **~20 minut před** touto minutou:

\[
generator\_minute\_utc = (import\_minute\_utc - 20) \bmod 60
\]

### Důležitá poznámka k časové zóně
- BigQuery DTS transfery běží **v UTC** (nemění se s DST).
- Pokud Deepnote umožňuje plánovat v **UTC**, použij UTC (nejstabilnější).
- Pokud Deepnote plánuje v **lokálním čase** (`Europe/Prague`), převeď cron z UTC na lokální čas:
  - **CET (zima)**: `local = UTC + 1h` (např. `50 4-14` UTC → `50 5-15` local)
  - **CEST (léto)**: `local = UTC + 2h` (např. `50 4-14` UTC → `50 6-16` local)

### Doporučené schedule (UTC) per tenant / country
Okna jsou nastavená tak, aby:
- CZ generátor běžel v rámci „business hours“ (cca 06:00–17:00 Prague) a stihl propsat D‑1 před 07:00.
- non‑CZ generátor začínal až tak, aby se D‑1 začal objevovat až **po 14:00 Prague**.

#### Denatura (`denatura-main`)
- **denatura_cz** (import `:10` UTC → generator `:50` UTC): `50 4-14 * * *`
- **denatura_sk** (import `:10` UTC → generator `:50` UTC): `50 12-14 * * *`

#### Autodoplnky (GCS CSV producer pro readiness)
Pozn.: Nejde o “forecast generator”, ale o producer, který zapisuje GCS CSV importované do `6_*` tabulek.

- **autodoplnky_cz** (import `:10` UTC → producer `:50` UTC): `50 4-14 * * *`

#### Proteinaco (`proteinaco-main`)
- **proteinaco_cz** (import `:40` UTC → generator `:20` UTC): `20 5-15 * * *`
- **proteinaco_sk** (import `:40` UTC → generator `:20` UTC): `20 13-15 * * *`
- **proteinaco_hu** (import `:40` UTC → generator `:20` UTC): `20 13-15 * * *`
- **proteinaco_pl** (import `:40` UTC → generator `:20` UTC): `20 13-15 * * *`
- **proteinaco_ro** (import `:32` UTC → generator `:12` UTC): `12 13-15 * * *`

#### Ruzovyslon (`ruzovyslon-main`)
- **ruzovyslon_cz** (import `:56` UTC → generator `:36` UTC): `36 5-15 * * *`
- **ruzovyslon_sk** (import `:50` UTC → generator `:30` UTC): `30 13-15 * * *`
- **ruzovyslon_hu** (import `:50` UTC → generator `:30` UTC): `30 13-15 * * *`
- **ruzovyslon_hr** (import `:50` UTC → generator `:30` UTC): `30 13-15 * * *`
- **ruzovyslon_ro** (import `:50` UTC → generator `:30` UTC): `30 13-15 * * *`
- **ruzovyslon_bg** (import `:50` UTC → generator `:30` UTC): `30 13-15 * * *`
- **ruzovyslon_si** (import `:50` UTC → generator `:30` UTC): `30 13-15 * * *`

#### Cerano / Livero (`cerano-main`)
Pozn.: `13_*` importy zde typicky startují v **`:10` UTC** (např. `05:10Z`). Schedule níže je nastavená tak, aby se CSV přepsalo ~20 min před importem.

- **cerano_cz** (import `:10` UTC → generator `:50` UTC): `50 4-14 * * *`
- **cerano_sk** (import `:10` UTC → generator `:50` UTC): `50 12-14 * * *`
- **cerano_hu** (import `:10` UTC → generator `:50` UTC): `50 12-14 * * *`
- **cerano_pl** (import `:10` UTC → generator `:50` UTC): `50 12-14 * * *`
- **livero_cz** (import `:10` UTC → generator `:50` UTC): `50 4-14 * * *`
- **livero_sk** (import `:10` UTC → generator `:50` UTC): `50 12-14 * * *`

### Ruzovyslon — které Deepnote notebooky přenastavit
Notebook mapping je v:
- `merglbot-ruzovyslon/data-pipelines/config/notebooks/ruzovyslon_marketing_forecast_notebooks.json`

Prakticky:
- CZ: `forecasting_cz` (soubor `forecasting_new_monhly_plan_diss_14_03_2025.ipynb`) → použij CZ cron
- non‑CZ: `forecasting_sk|hu|bg|hr|ro|si` → použij non‑CZ cron

### Kdy to nestačí (a co dělat)
- Pokud generátor logikou občas vyrobí D‑2 (např. kvůli GA4 latency / cutoff logice), samotný cron to úplně neeliminuje.
- Guardrails jsou **check-only** (alert/triage) — data se opravují u zdroje (generátor / upstream data) a pak se nechá doběhnout DTS import.
