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
- Pokud Deepnote plánuje v **lokálním čase**, doporučení níže ber jako **zimní (CET) přepočet** a v létě (CEST) počítej s posunem o +1h vůči UTC.

### Doporučené schedule (UTC) per tenant / country
Okna jsou nastavená tak, aby:
- CZ generátor běžel v rámci „business hours“ (cca 06:00–17:00 Prague) a stihl propsat D‑1 před 07:00.
- non‑CZ generátor začínal až tak, aby se D‑1 začal objevovat až **po 14:00 Prague**.

#### Denatura (`denatura-main`)
- **denatura_cz** (import `:10` UTC → generator `:50` UTC): `50 4-14 * * *`
- **denatura_sk** (import `:10` UTC → generator `:50` UTC): `50 12-14 * * *`

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
Pozn.: `13_*` importy zde typicky startují v **`:00` UTC** (ale některé configy mají 4h cadence). Schedule níže je „best effort“; SLA pro CZ ráno zajišťuje self‑heal guardrail.

- **cerano_cz**: `40 5-15 * * *`
- **cerano_sk**: `40 13-15 * * *`
- **cerano_hu**: `40 13-15 * * *`
- **cerano_pl**: `40 13-15 * * *`
- **livero_cz**: `40 5-15 * * *`
- **livero_sk**: `40 13-15 * * *`

### Ruzovyslon — které Deepnote notebooky přenastavit
Notebook mapping je v:
- `merglbot-ruzovyslon/data-pipelines/config/notebooks/ruzovyslon_marketing_forecast_notebooks.json`

Prakticky:
- CZ: `forecasting_cz` (soubor `forecasting_new_monhly_plan_diss_14_03_2025.ipynb`) → použij CZ cron
- non‑CZ: `forecasting_sk|hu|bg|hr|ro|si` → použij non‑CZ cron

### Kdy to nestačí (a proč máme self‑heal)
- Pokud generátor logikou občas vyrobí D‑2 (např. kvůli GA4 latency / cutoff logice), samotný cron to úplně neeliminuje.
- Proto je v plánu guardrail, který v 06:05 (CZ) a 14:05 (non‑CZ) ověří konzistenci a případně opraví `13_*` CSV z `final_prep_*`.

