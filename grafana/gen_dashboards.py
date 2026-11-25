"""Generate the Speculators CI Grafana dashboards (training benchmarks + acceptance).

Usage (from the repo root):
    python grafana/gen_dashboards.py
writes grafana/dashboards/*.json and grafana/k8s/speculators-ci-grafana.yaml.

Design notes:
- One Infinity query per dashboard (panel 1). Every other panel reuses its result through the
  built-in "-- Dashboard --" datasource, so metrics.json is downloaded once per refresh for the
  panels (plus once per dropdown).
- All dropdown filtering happens inside the Infinity JSONata root selector with `:doublequote`
  formatting. Panel transformations only use plain equality on single (repeat) values, so no
  regex escaping goes through the Scenes JSON.stringify/parse round trip (Grafana 13 bug).
"""

import json
from pathlib import Path

DS = {"type": "yesoreyeram-infinity-datasource", "uid": "dg3hq8oc1y8e8c"}
DASH_DS = {"type": "datasource", "uid": "-- Dashboard --"}
URL = "https://raw.githubusercontent.com/orestis-z/speculators-metrics/metrics-data/metrics.json"
BENCH_FILE = "tests/e2e/regression/test_training_benchmark.py"

# JSONata: derive short, human-readable labels for every record.
# JSONata `$name` locals must not share a name with a dashboard variable (branch, env, test, file):
# Grafana treats `$env` as a dashboard-variable reference, interpolates it and adds a dependency.
DERIVE = r"""
  $short := function($t){
    $replace($replace($replace($replace($replace($replace($t,
      /^test_(bench_)?/, ""),
      /[A-Za-z0-9_.-]+\//, ""),
      /speculators-ci-datasets:/, ""),
      /-(extra_train_args|target_layer_ids|acceptance_thresholds)[0-9]+|-None/, ""),
      /-hf:/, ":"),
      /:/, " · ")
  };
  $fileLabel := function($f){
    $replace($replace($replace($f, /^tests\/e2e\//, ""), /\/test_/, " / "), /\.py$/, "")
  };
  $rows := $map($, function($r){ (
    $envLabel := $r.python_version
      ? ("py" & $r.python_version & " / vLLM " & ($r.vllm_version ? $r.vllm_version : "?"))
      : "local";
    $testLabel := $short($r.test_id);
    $merge([$r, {
      "branch": $r.branch ? $r.branch : "local",
      "env": $envLabel,
      "test": $testLabel,
      "file": $fileLabel($r.test_file),
      "series": $testLabel & " · " & $envLabel,
      "part": $contains($r.metric, /^bench_[a-z0-9]+_ms$/)
        ? $replace($replace($r.metric, /^bench_/, ""), /_ms$/, ""),
      "rank": $contains($r.metric, /^bench_[a-z0-9]+_ms$/)
        ? $lookup({"fetch": 1, "fwd": 2, "bwd": 3, "clip": 4, "opt": 5, "queue": 6, "h2d": 7, "step": 8},
                  $replace($replace($r.metric, /^bench_/, ""), /_ms$/, "")),
      "pos": $contains($r.metric, /^acceptance_at_token_[0-9]+$/)
        ? $replace($r.metric, /^acceptance_at_token_/, ""),
      "pr_number": $r.pr_number ? $r.pr_number : "",
      "build_url": $r.build_url ? $r.build_url : "",
      "hostname": $r.hostname ? $r.hostname : "",
      "gpu_name": $r.gpu_name ? $r.gpu_name : "",
      "node_name": $r.node_name ? $r.node_name : ""
    }])
  )});
"""

PHASE_COLORS = {
    "fetch": "purple", "fwd": "blue", "bwd": "green", "clip": "yellow", "opt": "orange",
    "queue": "light-purple", "h2d": "super-light-purple",
}

# Default "Test file" selection: the regression suites. Smoke/connector tests only check that the
# pipeline runs (random or barely-trained drafters), so their acceptance is ~0 by design.
# Grafana defaults are static values: add new regression files here.
REGRESSION_FILES = ["regression / eagle3_conversion_acceptance", "regression / eagle3_offline_acceptance", "regression / eagle3_online_acceptance", "regression / mtp_online_acceptance", "regression / training_only_acceptance"]

FAMILY = {
    "bench": f'test_file = "{BENCH_FILE}"',
    "acc": f'test_file != "{BENCH_FILE}"',
}


TIME_RANGE = 'timestamp >= "${__from:date:iso}" and timestamp <= "${__to:date:iso}"'


def root_selector(family, filters):
    """filters: list of (field, variable) pairs applied as `field in [${var:doublequote}]`.

    Timestamps are UTC ISO strings ("...+00:00"), so string comparison against the ISO range
    is exact; jsonata-go's $toMillis drops the time of day.
    """
    cond = " and ".join([FAMILY[family], TIME_RANGE]
                        + [f"{f} in [${{{v}:doublequote}}]" for f, v in filters])
    return "(\n" + DERIVE + f"  $rows[{cond}]\n)"


def distinct_selector(family, field, filters):
    cond = " and ".join([FAMILY[family]] + [f"{f} in [${{{v}:doublequote}}]" for f, v in filters])
    return (
        "(\n" + DERIVE + f"  $map($distinct($rows[{cond}].{field}), function($v){{ {{\"v\": $v}} }})\n)"
    )


STRING_COLS = [
    "metric", "series", "test", "env", "file", "part", "pos", "branch", "test_id", "test_file",
    "git_sha", "git_sha_short", "pr_number", "build_url", "python_version", "vllm_version",
    "transformers_version", "gpu_name", "node_name", "hostname", "num_gpus", "total_seq_len",
    "optimizer", "speculator_type",
]


def infinity_target(selector, columns=None):
    cols = columns or (
        [{"selector": "timestamp", "text": "Time", "type": "timestamp"},
         {"selector": "value", "text": "value", "type": "number"}]
        + [{"selector": c, "text": c, "type": "string"} for c in STRING_COLS]
        + [{"selector": "rank", "text": "rank", "type": "number"}]
    )
    return {
        "refId": "A",
        "datasource": DS,
        "type": "json",
        "source": "url",
        "parser": "backend",
        "format": "table",
        "url": URL,
        "url_options": {"method": "GET", "data": ""},
        "root_selector": selector,
        "columns": [{**c, "timestampFormat": ""} for c in cols],
        "filters": [],
    }


def query_variable(name, label, selector, current, sort=1):
    return {
        "type": "query",
        "name": name,
        "label": label,
        "datasource": DS,
        "query": {
            "queryType": "infinity",
            "refId": "variable",
            "infinityQuery": {
                **infinity_target(selector, [{"selector": "v", "text": "v", "type": "string"}]),
                "refId": "variable",
            },
        },
        "refresh": 1,  # on dashboard load
        "multi": True,
        "includeAll": True,
        "current": current,
        "options": [],
        "sort": sort,
        "regex": "",
        "hide": 0,
    }


ALL = {"text": ["All"], "value": ["$__all"]}


def sel(*vals):
    return {"text": list(vals), "value": list(vals)}


def filt(match, *pairs, matcher="equal"):
    return {
        "id": "filterByValue",
        "options": {
            "type": "include",
            "match": match,
            "filters": [
                {"fieldName": f, "config": {"id": matcher, "options": {"value": v}}} for f, v in pairs
            ],
        },
    }


def partition(*fields):
    return {
        "id": "partitionByValues",
        "options": {"fields": list(fields), "keepFields": False, "naming": {"asLabels": True}},
    }


LINKS = [
    {"title": "Commit ${__data.fields.git_sha_short}", "targetBlank": True,
     "url": "https://github.com/vllm-project/speculators/commit/${__data.fields.git_sha}"},
    {"title": "Buildkite build", "targetBlank": True, "url": "${__data.fields.build_url}"},
    {"title": "PR #${__data.fields.pr_number}", "targetBlank": True,
     "url": "https://github.com/vllm-project/speculators/pull/${__data.fields.pr_number}"},
]


def timeseries(pid, title, grid, unit, transformations, *, targets=None, datasource=None,
               display="${__field.labels.series}", description="", stack=False, overrides=None,
               decimals=None, repeat=None, legend_calcs=("lastNotNull", "min", "max"),
               legend_placement="bottom", legend_sort="Name"):
    custom = {
        "drawStyle": "line", "lineWidth": 2, "fillOpacity": 40 if stack else 0,
        "showPoints": "always", "pointSize": 5, "spanNulls": True, "lineInterpolation": "linear",
        "stacking": {"mode": "normal" if stack else "none", "group": "A"},
        "axisPlacement": "auto", "axisSoftMin": 0 if stack else None,
    }
    custom = {k: v for k, v in custom.items() if v is not None}
    p = {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "description": description,
        "gridPos": grid,
        "datasource": datasource or DS,
        "targets": targets,
        # rank is a helper column for ordering; never plot it
        "transformations": transformations
        + [{"id": "organize", "options": {"excludeByName": {"rank": True}}}],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "displayName": display,
                "links": LINKS,
                "custom": custom,
                # same series name -> same color in every panel
                "color": {"mode": "palette-classic-by-name"},
                **({"decimals": decimals} if decimals is not None else {}),
            },
            "overrides": overrides or [],
        },
        "options": {
            "legend": {"showLegend": True, "displayMode": "table", "placement": legend_placement,
                       "calcs": list(legend_calcs),
                       **({"sortBy": legend_sort, "sortDesc": False} if legend_sort else {})},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }
    if repeat:
        p.update(repeat)
    return p


def dash_target(source_panel=1):
    return [{"refId": "A", "datasource": DASH_DS, "panelId": source_panel, "withTransforms": False}]


def row(pid, title, y, repeat=None, collapsed=False, panels=None):
    r = {"id": pid, "type": "row", "title": title, "collapsed": collapsed,
         "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": panels or []}
    if repeat:
        r["repeat"] = repeat
    return r


def raw_table(pid, y, cols):
    return {
        "id": pid,
        "type": "table",
        "title": "Raw records",
        "gridPos": {"h": 14, "w": 24, "x": 0, "y": y},
        "datasource": DASH_DS,
        "targets": dash_target(),
        "transformations": [
            {"id": "sortBy", "options": {"sort": [{"field": "Time", "desc": True}]}},
            {"id": "organize", "options": {
                "excludeByName": {c: True for c in ("series", "part", "pos", "rank")},
                "indexByName": {c: i for i, c in enumerate(cols)},
            }},
        ],
        "fieldConfig": {
            "defaults": {"custom": {"filterable": True}},
            "overrides": [
                {"matcher": {"id": "byName", "options": "git_sha_short"},
                 "properties": [{"id": "links", "value": [LINKS[0]]}]},
                {"matcher": {"id": "byName", "options": "build_url"},
                 "properties": [{"id": "links", "value": [LINKS[1]]}]},
                {"matcher": {"id": "byName", "options": "pr_number"},
                 "properties": [{"id": "links", "value": [LINKS[2]]}]},
            ],
        },
        "options": {"showHeader": True, "cellHeight": "sm"},
    }


def dashboard(uid, title, description, variables, panels, tags):
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "tags": tags,
        "timezone": "utc",
        "editable": True,
        "graphTooltip": 1,
        "time": {"from": "now-30d", "to": "now"},
        "refresh": "",
        "schemaVersion": 41,
        "templating": {"list": variables},
        "annotations": {"list": []},
        "links": [
            {"title": "Speculators CI", "type": "dashboards", "tags": ["speculators-ci"],
             "asDropdown": False, "includeVars": True, "keepTime": True},
        ],
        "panels": panels,
    }


# ---------------------------------------------------------------- training benchmarks

def bench():
    filters = [("branch", "branch"), ("env", "env"), ("test", "test")]
    variables = [
        query_variable("branch", "Branch", distinct_selector("bench", "branch", []), sel("main")),
        query_variable("env", "Environment", distinct_selector("bench", "env", [("branch", "branch")]),
                       sel("py3.12 / vLLM latest")),
        query_variable("test", "Test",
                       distinct_selector("bench", "test", [("branch", "branch"), ("env", "env")]), ALL),
    ]
    metric = lambda m: filt("all", ("metric", m))
    ser = [partition("series")]
    panels = [
        timeseries(1, "Step time", {"h": 9, "w": 12, "x": 0, "y": 0}, "ms",
                   [metric("bench_step_ms"), *ser],
                   targets=[infinity_target(root_selector("bench", filters))],
                   description="Wall time of one optimizer step (bench_step_ms). Lower is better."),
        timeseries(2, "Throughput (tokens/s)", {"h": 9, "w": 12, "x": 12, "y": 0}, "short",
                   [metric("bench_tokens_per_s"), *ser], targets=dash_target(), datasource=DASH_DS,
                   description="bench_tokens_per_s. Higher is better."),
        timeseries(3, "Effective throughput (tokens/s)", {"h": 9, "w": 8, "x": 0, "y": 9}, "short",
                   [metric("bench_effective_tokens_per_s"), *ser], targets=dash_target(),
                   datasource=DASH_DS, description="bench_effective_tokens_per_s."),
        timeseries(4, "Peak allocated memory", {"h": 9, "w": 8, "x": 8, "y": 9}, "decmbytes",
                   [metric("bench_peak_allocated_mb"), *ser], targets=dash_target(),
                   datasource=DASH_DS, description="bench_peak_allocated_mb."),
        timeseries(5, "Data fetch share of step", {"h": 9, "w": 8, "x": 16, "y": 9}, "percentunit",
                   [metric("bench_fetch_frac"), *ser], targets=dash_target(), datasource=DASH_DS,
                   description="bench_fetch_frac = fetch_ms / step_ms."),
        row(6, "Step breakdown · $test", 18, repeat="test"),
        timeseries(
            7, "$env", {"h": 10, "w": 8, "x": 0, "y": 19}, "ms",
            [
                filt("all", ("test", "${test}"), ("env", "${env}")),
                filt("all", ("metric", "^bench_(fetch|fwd|bwd|clip|opt|queue|h2d)_ms$"), matcher="regex"),
                # stable sort by phase rank so the stack is fetch, fwd, bwd, clip, opt (bottom to top)
                {"id": "sortBy", "options": {"sort": [{"field": "rank"}]}},
                partition("part"),
            ],
            targets=dash_target(), datasource=DASH_DS, display="${__field.labels.part}", stack=True,
            description=(
                "Stacked: fetch + fwd + bwd + clip + opt ≈ step_ms. Dashed lines: queue and h2d, "
                "which are parts of fetch (not stacked)."
            ),
            overrides=[
                {"matcher": {"id": "byName", "options": n}, "properties": [
                    {"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
                for n, c in PHASE_COLORS.items()
            ] + [
                {"matcher": {"id": "byName", "options": n}, "properties": [
                    {"id": "custom.stacking", "value": {"mode": "none", "group": "A"}},
                    {"id": "custom.fillOpacity", "value": 0},
                    {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                ]} for n in ("queue", "h2d")
            ],
            repeat={"repeat": "env", "repeatDirection": "h", "maxPerRow": 3},
            legend_calcs=("lastNotNull", "mean"), legend_placement="right", legend_sort=None,
        ),
        row(8, "Raw records", 29, collapsed=True, panels=[raw_table(9, 30, [
            "Time", "test", "env", "metric", "value", "branch", "git_sha_short", "pr_number",
            "build_url", "python_version", "vllm_version", "gpu_name", "node_name", "hostname",
            "num_gpus", "total_seq_len", "optimizer", "speculator_type", "test_id", "test_file"])]),
    ]
    # field matching: breakdown panel uses display = part label
    for p in panels:
        if p["id"] == 7:
            p["fieldConfig"]["defaults"]["links"] = LINKS
    return dashboard(
        "speculators-ci-bench", "Speculators CI · Training benchmarks",
        "Training step benchmarks from tests/e2e/regression/test_training_benchmark.py",
        variables, panels, ["speculators-ci"],
    )


# ---------------------------------------------------------------- acceptance

def acc():
    filters = [("branch", "branch"), ("env", "env"), ("file", "file"), ("test", "test")]
    variables = [
        query_variable("branch", "Branch", distinct_selector("acc", "branch", []), sel("main")),
        query_variable("env", "Environment", distinct_selector("acc", "env", [("branch", "branch")]),
                       sel("py3.12 / vLLM latest")),
        query_variable("file", "Test file",
                       distinct_selector("acc", "file", [("branch", "branch"), ("env", "env")]),
                       sel(*REGRESSION_FILES)),
        query_variable("test", "Test", distinct_selector(
            "acc", "test", [("branch", "branch"), ("env", "env"), ("file", "file")]), ALL),
    ]
    summary = {
        "id": 1,
        "type": "table",
        "title": "Acceptance length · latest run per test",
        "description": "Last, min and max acceptance_length over the selected time range.",
        "gridPos": {"h": 13, "w": 24, "x": 0, "y": 0},
        "datasource": DS,
        "targets": [infinity_target(root_selector("acc", filters))],
        "transformations": [
            filt("all", ("metric", "acceptance_length")),
            {"id": "sortBy", "options": {"sort": [{"field": "Time"}]}},
            {"id": "groupBy", "options": {"fields": {
                "file": {"aggregations": [], "operation": "groupby"},
                "test": {"aggregations": [], "operation": "groupby"},
                "env": {"aggregations": [], "operation": "groupby"},
                "value": {"aggregations": ["lastNotNull", "min", "max", "count"], "operation": "aggregate"},
                "Time": {"aggregations": ["lastNotNull"], "operation": "aggregate"},
                "git_sha_short": {"aggregations": ["lastNotNull"], "operation": "aggregate"},
            }}},
            {"id": "organize", "options": {"indexByName": {
                "file": 0, "test": 1, "env": 2, "value (lastNotNull)": 3, "value (min)": 4,
                "value (max)": 5, "value (count)": 6, "Time (lastNotNull)": 7,
                "git_sha_short (lastNotNull)": 8,
            }, "renameByName": {
                "file": "Test file", "test": "Test", "env": "Environment",
                "value (lastNotNull)": "Latest", "value (min)": "Min", "value (max)": "Max",
                "value (count)": "Runs", "Time (lastNotNull)": "Last run",
                "git_sha_short (lastNotNull)": "Commit",
            }}},
            {"id": "sortBy", "options": {"sort": [{"field": "Test file"}]}},
        ],
        "fieldConfig": {
            "defaults": {"decimals": 2, "custom": {"filterable": True}},
            "overrides": [
                {"matcher": {"id": "byName", "options": "Last run"},
                 "properties": [{"id": "unit", "value": "dateTimeAsIso"}]},
                {"matcher": {"id": "byName", "options": "Runs"},
                 "properties": [{"id": "decimals", "value": 0}]},
            ] + [
                {"matcher": {"id": "byName", "options": name},
                 "properties": [{"id": "custom.width", "value": width}]}
                for name, width in {
                    "Test file": 290, "Test": 440, "Environment": 170, "Latest": 80, "Min": 80,
                    "Max": 80, "Runs": 60, "Last run": 170, "Commit": 90,
                }.items()
            ],
        },
        "options": {"showHeader": True, "cellHeight": "sm"},
    }
    length_panel = timeseries(
        3, "Acceptance length", {"h": 11, "w": 16, "x": 0, "y": 14}, "none",
        [filt("all", ("file", "${file}"), ("metric", "acceptance_length")), partition("series")],
        targets=dash_target(), datasource=DASH_DS, decimals=2,
        description="Mean accepted tokens per draft step (acceptance_length). Higher is better.",
    )
    pos_panel = {
        "id": 4,
        "type": "barchart",
        "title": "Acceptance by draft position · latest run",
        "description": "acceptance_at_token_k of the most recent run per test, k = draft position.",
        "gridPos": {"h": 11, "w": 8, "x": 16, "y": 14},
        "datasource": DASH_DS,
        "targets": dash_target(),
        "transformations": [
            filt("all", ("file", "${file}")),
            filt("all", ("metric", "^acceptance_at_token_"), matcher="regex"),
            {"id": "sortBy", "options": {"sort": [{"field": "Time"}]}},
            {"id": "groupBy", "options": {"fields": {
                "pos": {"aggregations": [], "operation": "groupby"},
                "series": {"aggregations": [], "operation": "groupby"},
                "value": {"aggregations": ["lastNotNull"], "operation": "aggregate"},
            }}},
            {"id": "groupingToMatrix", "options": {
                "columnField": "series", "rowField": "pos", "valueField": "value (lastNotNull)",
                "emptyValue": "null"}},
            {"id": "organize", "options": {"renameByName": {"pos\\series": "position"}}},
            {"id": "sortBy", "options": {"sort": [{"field": "position"}]}},
        ],
        "fieldConfig": {"defaults": {"decimals": 2, "color": {"mode": "palette-classic-by-name"},
                                     "custom": {"fillOpacity": 80}},
                        "overrides": []},
        "options": {"xField": "position", "orientation": "vertical", "showValue": "never",
                    "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"}, "groupWidth": 0.8, "barWidth": 0.95},
    }
    panels = [
        summary,
        row(2, "$file", 13, repeat="file"),
        length_panel,
        pos_panel,
        row(5, "Raw records", 25, collapsed=True, panels=[raw_table(6, 26, [
            "Time", "file", "test", "env", "metric", "value", "branch", "git_sha_short",
            "pr_number", "build_url", "python_version", "vllm_version", "transformers_version",
            "gpu_name", "node_name", "hostname", "test_id", "test_file"])]),
    ]
    return dashboard(
        "speculators-ci-acceptance", "Speculators CI · Acceptance",
        "Speculative decoding acceptance from the e2e smoke and regression tests",
        variables, panels, ["speculators-ci"],
    )


DATASOURCE_CR = """\
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaDatasource
metadata:
  name: speculators-metrics-infinity
  namespace: grafana-monitoring
spec:
  instanceSelector:
    matchLabels:
      dashboards: grafana  # TODO: use the label of the cluster's Grafana CR
  plugins:
    - name: yesoreyeram-infinity-datasource
      version: "4.1.8"
  datasource:
    name: speculators-metrics
    type: yesoreyeram-infinity-datasource
    uid: dg3hq8oc1y8e8c  # referenced by the dashboards
    access: proxy
    jsonData: {}
"""


def dashboard_cr(d):
    body = json.dumps(d, indent=2, ensure_ascii=False)
    indented = "\n".join(("    " + line) if line else line for line in body.splitlines())
    return f"""\
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaDashboard
metadata:
  name: {d["uid"]}
  namespace: grafana-monitoring
spec:
  instanceSelector:
    matchLabels:
      dashboards: grafana  # TODO: use the label of the cluster's Grafana CR
  plugins:
    - name: yesoreyeram-infinity-datasource
      version: "4.1.8"
  json: |
{indented}
"""


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    dashboards = [bench(), acc()]
    for d in dashboards:
        path = root / "dashboards" / f"{d['uid']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
        print("wrote", path.relative_to(root.parent))
    cr = root / "k8s" / "speculators-ci-grafana.yaml"
    cr.parent.mkdir(parents=True, exist_ok=True)
    cr.write_text("---\n".join([DATASOURCE_CR] + [dashboard_cr(d) for d in dashboards]))
    print("wrote", cr.relative_to(root.parent))
