# Speculators CI Metrics

CI metrics for [vllm-project/speculators](https://github.com/vllm-project/speculators) and the Grafana dashboards that display them.

| Branch | Contents |
| --- | --- |
| [`metrics-data`](https://github.com/orestis-z/speculators-metrics/tree/metrics-data) | `metrics.json`: one record per metric value, appended by CI (`scripts/ci/push_metrics.py` in the speculators repo). Data model and CI setup are documented in that branch's README. |
| `main` | [`grafana/`](grafana/): dashboard generator, generated dashboard JSON, and Grafana Operator manifests. |

Grafana reads the data through the [Infinity](https://grafana.com/grafana/plugins/yesoreyeram-infinity-datasource/) plugin from:

```
https://raw.githubusercontent.com/orestis-z/speculators-metrics/metrics-data/metrics.json
```
