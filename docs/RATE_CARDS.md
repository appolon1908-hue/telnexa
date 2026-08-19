# Rate cards

`rates` stores provider cost and customer sell rates with tenant/plan scope, country, network, destination prefix, provider/connector, priority, effective interval, currency and decimal price per segment. Resolution ranks tenant override, plan match, longest prefix, then priority. Message rows retain both resolved snapshots; historical profitability never uses current rates. CSV import must validate currency, prefixes, overlapping dates, and margin in a transaction before activation.
